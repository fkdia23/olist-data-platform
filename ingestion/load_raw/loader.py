# ingestion/load_raw/loader.py
"""Cœur de l'ingestion : valider une page, rejeter, insérer sans doublon, avancer le watermark."""

import logging
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from ingestion.load_raw.tables import PARTITIONED, TableSpec

log = logging.getLogger(__name__)


@dataclass
class Stats:
    """Compteurs d'une table ; ignored = valides déjà présents (preuve d'idempotence)."""

    read: int = 0
    valid: int = 0
    rejected: int = 0
    inserted: int = 0

    @property
    def ignored(self) -> int:
        return self.valid - self.inserted

    def add(self, other: "Stats") -> None:
        self.read += other.read
        self.valid += other.valid
        self.rejected += other.rejected
        self.inserted += other.inserted


def describe_errors(exc: ValidationError) -> str:
    """Résume les erreurs Pydantic en une ligne : 'order_purchase_timestamp: Value error, …'."""
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def get_watermark(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute("select last_src_id from obs.watermarks where table_name = %s", (table,)).fetchone()
    return row[0] if row else 0


def reset_watermarks(conn: psycopg.Connection, tables: list[str]) -> None:
    """Repart de zéro : la prochaine lecture reprend toute la source (test d'idempotence)."""
    conn.execute("delete from obs.watermarks where table_name = any(%s)", (tables,))


def load_page(
    conn: psycopg.Connection, spec: TableSpec, rows: list[dict], next_after_id: int, batch_id: int
) -> Stats:
    """Traite une page dans UNE transaction : données, rejets et watermark avancent ensemble."""
    stats = Stats(read=len(rows))
    raw_by_id = {r["_src_id"]: r for r in rows}  # la ligne brute, pour l'archiver si rejetée
    valid: list[tuple[int, object]] = []
    rejects: list[tuple[int, str]] = []

    # 1. Contrat : type, format et valeurs permises, ligne par ligne
    for row in rows:
        try:
            valid.append((row["_src_id"], spec.contract.model_validate(row)))
        except ValidationError as exc:
            rejects.append((row["_src_id"], describe_errors(exc)))

    with conn.transaction():  # tout ou rien pour cette page
        # 2. Intégrité référentielle : la valeur doit exister dans la table raw référencée
        for column, (ref_table, ref_column) in spec.references.items():
            keys = list({getattr(model, column) for _, model in valid})
            if not keys:
                continue
            found = {
                r[0]
                for r in conn.execute(
                    f"select distinct {ref_column} from {ref_table} where {ref_column} = any(%s)", (keys,)
                )
            }
            kept = []
            for src_id, model in valid:
                if getattr(model, column) in found:
                    kept.append((src_id, model))
                else:
                    rejects.append((src_id, f"{column}: absent de {ref_table}"))
            valid = kept

        # 3. Partitions : les mois de cette page doivent exister avant l'insertion
        if spec.name == "orders" and valid:
            dates = [m.order_purchase_timestamp for _, m in valid]
            for parent in PARTITIONED:
                conn.execute(
                    "select raw.ensure_monthly_partitions(%s, %s, %s)",
                    (parent, min(dates).date(), max(dates).date()),
                )

        # 4. Chargement rapide dans une table temporaire avec COPY
        cols = ", ".join(spec.columns)
        conn.execute(
            f"create temp table tmp_load on commit drop as "
            f"select {cols}, _src_id from raw.{spec.name} with no data"
        )
        with conn.cursor().copy(f"copy tmp_load ({cols}, _src_id) from stdin") as copy:
            for src_id, model in valid:
                copy.write_row([getattr(model, col) for col in spec.columns] + [src_id])

        # 5. Insertion idempotente : une ligne déjà présente est ignorée, pas dupliquée
        if spec.name == "geolocation":
            insert = (
                f"insert into raw.geolocation (row_hash, {cols}, _src_id, _batch_id) "
                f"select md5(concat_ws('|', {cols})), {cols}, _src_id, %s from tmp_load "
                f"on conflict do nothing"
            )
        elif spec.needs_order_date:
            t_cols = ", ".join(f"t.{col}" for col in spec.columns)
            insert = (
                f"insert into raw.{spec.name} ({cols}, order_purchase_timestamp, _src_id, _batch_id) "
                f"select {t_cols}, o.order_purchase_timestamp, t._src_id, %s "
                f"from tmp_load t join raw.orders o on o.order_id = t.order_id "
                f"on conflict do nothing"
            )
        else:
            insert = (
                f"insert into raw.{spec.name} ({cols}, _src_id, _batch_id) "
                f"select {cols}, _src_id, %s from tmp_load on conflict do nothing"
            )
        stats.inserted = conn.execute(insert, (batch_id,)).rowcount

        # 6. Rejets archivés (une seule fois par ligne source grâce à la clé primaire)
        if rejects:
            with conn.cursor() as cur:
                cur.executemany(
                    "insert into raw.rejects (table_name, src_id, reason, payload, _batch_id) "
                    "values (%s, %s, %s, %s, %s) on conflict do nothing",
                    [
                        (spec.name, src_id, reason, Jsonb(raw_by_id[src_id]), batch_id)
                        for src_id, reason in rejects
                    ],
                )

        # 7. Watermark : la prochaine exécution reprendra après cette page
        conn.execute(
            "insert into obs.watermarks (table_name, last_src_id) values (%s, %s) "
            "on conflict (table_name) do update set last_src_id = excluded.last_src_id, updated_at = now()",
            (spec.name, next_after_id),
        )

    stats.valid = len(valid)
    stats.rejected = len(rejects)
    return stats
