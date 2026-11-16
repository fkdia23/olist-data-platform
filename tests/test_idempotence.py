# tests/test_idempotence.py
"""Test d'intégration : demande les bases et l'API démarrées (make up, make migrate, make generate)."""

import os
import subprocess
import sys

import psycopg
import pytest

pytestmark = pytest.mark.integration  # tous les tests du fichier portent ce marqueur


def run_loader(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "ingestion.load_raw", *args], check=True)


def last_metric(conn: psycopg.Connection, metric: str) -> float:
    row = conn.execute(
        "select value from obs.run_metrics where step = 'load_raw' and metric = %s "
        "order by recorded_at desc limit 1",
        (metric,),
    ).fetchone()
    return row[0]


def test_second_full_run_inserts_nothing():
    run_loader("--tables", "sellers", "products", "--full")
    run_loader("--tables", "sellers", "products", "--full")
    with psycopg.connect(os.environ["WAREHOUSE_DSN"]) as conn:
        assert last_metric(conn, "rows_read") > 0
        assert last_metric(conn, "rows_inserted") == 0
        assert last_metric(conn, "rows_ignored") == last_metric(conn, "rows_read")
