# ingestion/load_raw/__main__.py
"""Point d'entrée : uv run python -m ingestion.load_raw [--tables ...] [--full] [--page-size N]

Variables d'environnement : WAREHOUSE_DSN, SOURCE_API_URL, RUN_ID (optionnelle, fournie par le runner).
"""

import argparse
import logging
import os
import sys
import time

import psycopg

from ingestion.load_raw.loader import Stats, get_watermark, load_page, reset_watermarks
from ingestion.load_raw.source_client import SourceClient
from ingestion.load_raw.tables import BY_NAME, TABLES

log = logging.getLogger("load_raw")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestion source -> PostgreSQL 16 (schéma raw)")
    parser.add_argument(
        "--tables",
        nargs="+",
        choices=list(BY_NAME),
        default=list(BY_NAME),
        help="tables à charger (défaut : toutes, dans l'ordre des dépendances)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="ignore les watermarks et relit toute la source (test d'idempotence)",
    )
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument(
        "--fail-on-reject", action="store_true", help="code de sortie 3 si au moins une ligne est rejetée"
    )
    return parser.parse_args(argv)


def start_run(conn: psycopg.Connection) -> tuple[int, bool]:
    """Réutilise RUN_ID si le runner l'a fourni, sinon crée un run « manuel »."""
    if os.environ.get("RUN_ID"):
        return int(os.environ["RUN_ID"]), False
    row = conn.execute(
        "insert into obs.pipeline_runs (scenario, trigger, status, started_at) "
        "values ('load_raw', 'manuel', 'running', now()) returning run_id"
    ).fetchone()
    return row[0], True


def record(conn: psycopg.Connection, run_id: int, step: str, metrics: dict[str, float]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "insert into obs.run_metrics (run_id, step, metric, value) values (%s, %s, %s, %s)",
            [(run_id, step, name, value) for name, value in metrics.items()],
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # une ligne par requête HTTP, c'est trop
    args = parse_args(argv)
    specs = [spec for spec in TABLES if spec.name in args.tables]  # garde l'ordre des dépendances

    # autocommit=True : chaque « with conn.transaction() » est une vraie transaction
    conn = psycopg.connect(os.environ["WAREHOUSE_DSN"], autocommit=True)
    client = SourceClient(os.environ.get("SOURCE_API_URL", "http://localhost:8000"))
    run_id, own_run = start_run(conn)
    if args.full:
        reset_watermarks(conn, [s.name for s in specs])
        log.info("watermarks remis à zéro : relecture complète")

    total, started, status, error = Stats(), time.monotonic(), "success", None
    try:
        for spec in specs:
            table_stats, t0 = Stats(), time.monotonic()
            after_id = get_watermark(conn, spec.name)
            while True:
                page = client.fetch_page(spec.name, after_id, args.page_size)
                if page["count"] == 0:
                    break  # plus rien après le watermark
                table_stats.add(load_page(conn, spec, page["rows"], page["next_after_id"], run_id))
                after_id = page["next_after_id"]
            conn.execute(f"analyze raw.{spec.name}")  # statistiques à jour (volume par partition)
            seconds = time.monotonic() - t0
            log.info(
                "%-15s lues=%d valides=%d rejetées=%d insérées=%d ignorées=%d (%.1f s)",
                spec.name,
                table_stats.read,
                table_stats.valid,
                table_stats.rejected,
                table_stats.inserted,
                table_stats.ignored,
                seconds,
            )
            record(
                conn,
                run_id,
                f"load_raw:{spec.name}",
                {
                    "rows_read": table_stats.read,
                    "rows_valid": table_stats.valid,
                    "rows_rejected": table_stats.rejected,
                    "rows_inserted": table_stats.inserted,
                    "rows_ignored": table_stats.ignored,
                    "duration_s": seconds,
                },
            )
            total.add(table_stats)
    except Exception as exc:  # on enregistre l'échec avant de sortir
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.exception("ingestion interrompue")
    finally:
        seconds = time.monotonic() - started
        record(
            conn,
            run_id,
            "load_raw",
            {
                "rows_read": total.read,
                "rows_valid": total.valid,
                "rows_rejected": total.rejected,
                "rows_inserted": total.inserted,
                "rows_ignored": total.ignored,
                "duration_s": seconds,
                "rows_per_s": total.read / seconds if seconds else 0,
                "conformity_pct": 100.0 * total.valid / total.read if total.read else 100.0,
                "api_retries": client.retries,
            },
        )
        if own_run:
            conn.execute(
                "update obs.pipeline_runs set status = %s, finished_at = now(), error = %s where run_id = %s",
                (status, error, run_id),
            )
        client.close()
        conn.close()

    log.info(
        "TOTAL lues=%d valides=%d rejetées=%d insérées=%d ignorées=%d débit=%.0f lignes/s",
        total.read,
        total.valid,
        total.rejected,
        total.inserted,
        total.ignored,
        total.read / seconds if seconds else 0,
    )
    if status == "failed":
        return 1
    if args.fail_on_reject and total.rejected:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
