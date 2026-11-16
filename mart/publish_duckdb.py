# mart/publish_duckdb.py
"""Publie les tables du schéma marts (PostgreSQL 16) dans le fichier DuckDB du data mart.

uv run --env-file .env python -m mart.publish_duckdb

DuckDB lit PostgreSQL avec son extension postgres (ATTACH en lecture seule), copie chaque table,
puis le fichier temporaire remplace l'ancien d'un coup (os.replace) : un lecteur ne voit jamais
un fichier à moitié écrit. KPIs dans obs.run_metrics, step publish_duckdb, si RUN_ID est défini.
Variables : WAREHOUSE_DSN (obligatoire), DUCKDB_PATH, RUN_ID.
"""

import os
import pathlib
import time

import duckdb
import psycopg


def record(dsn: str, values: dict[str, float]) -> None:
    run_id = os.environ.get("RUN_ID")
    if not run_id:
        return                                         # lancé à la main : pas de run à compléter
    with psycopg.connect(dsn) as conn:
        conn.cursor().executemany(
            "insert into obs.run_metrics (run_id, step, metric, value) values (%s, 'publish_duckdb', %s, %s)",
            [(int(run_id), metric, float(value)) for metric, value in values.items()],
        )


def main() -> None:
    dsn = os.environ["WAREHOUSE_DSN"]
    target = pathlib.Path(os.environ.get("DUCKDB_PATH", "data/duckdb/mart.duckdb"))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)
    started = time.monotonic()

    with psycopg.connect(dsn) as conn:
        tables = [name for (name,) in conn.execute(
            "select table_name from information_schema.tables where table_schema = 'marts' order by 1")]
    if not tables:
        raise SystemExit("schéma marts vide : lance dbt build avant de publier")

    counts: dict[str, int] = {}
    with duckdb.connect(str(tmp)) as con:
        con.execute("INSTALL postgres")                # sans effet si l'extension est déjà là (image Docker)
        con.execute("LOAD postgres")
        con.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres, READ_ONLY)")
        for table in tables:
            con.execute(f'CREATE TABLE main."{table}" AS SELECT * FROM pg.marts."{table}"')
            counts[table] = con.execute(f'SELECT count(*) FROM main."{table}"').fetchone()[0]
            print(f"{table:<20} {counts[table]:>10} lignes", flush=True)
        con.execute("DETACH pg")
        con.execute("CHECKPOINT")                      # tout dans le fichier, plus rien dans le .wal
    os.replace(tmp, target)                            # bascule atomique vers la nouvelle version

    size = target.stat().st_size
    duration = time.monotonic() - started
    print(f"DuckDB : {target} publié ({size / 1e6:.1f} Mo, {len(tables)} tables, {duration:.1f} s)")
    record(dsn, {"duration_s": duration, "size_bytes": size, "tables": len(tables),
                 **{f"rows.{t}": n for t, n in counts.items()}})


if __name__ == "__main__":
    main()
