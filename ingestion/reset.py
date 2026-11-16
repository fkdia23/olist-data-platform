# ingestion/reset.py
"""Scénario 5 : remet la plateforme à blanc.

uv run python -m ingestion.reset                 # entrepôt (raw + tables dbt) + watermarks + DuckDB
uv run python -m ingestion.reset --with-source   # et vide aussi la base source (re-génération complète)

Les tables construites par dbt (intermediate, marts) sont vidées, pas supprimées : elles gardent
leur structure et leurs droits, et le prochain dbt build les remplit entièrement (le modèle
incrémental fct_order_items repart de zéro quand sa table est vide). Sans ce vidage, le data mart
gardait les articles d'avant le reset, qui n'existaient plus dans raw.

L'historique d'observabilité (obs.pipeline_runs, run_metrics, alerts) est conservé :
on veut voir dans Grafana que le reset a eu lieu.
"""

import argparse
import os
import pathlib

import psycopg
from psycopg import sql

from ingestion.load_raw.tables import PARTITIONED

PLAIN_TABLES = ("geolocation", "customers", "sellers", "products", "rejects")
DBT_SCHEMAS = ("intermediate", "marts")  # tables dbt ; staging n'a que des vues, audit garde l'historique
SOURCE_TABLES = (
    "geolocation",
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
    "generation_runs",
)


def drop_partitions(conn: psycopg.Connection) -> int:
    """Supprime chaque partition : instantané, là où un DELETE parcourrait chaque ligne."""
    rows = conn.execute(
        """
        select child.relname
        from pg_inherits i
        join pg_class child  on child.oid  = i.inhrelid
        join pg_class parent on parent.oid = i.inhparent
        join pg_namespace n  on n.oid = parent.relnamespace
        where n.nspname = 'raw' and parent.relname = any(%s) and child.relkind = 'r'
    """,
        (list(PARTITIONED),),
    ).fetchall()
    for (name,) in rows:
        conn.execute(sql.SQL("drop table raw.{}").format(sql.Identifier(name)))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Réinitialise l'entrepôt (scénario 5)")
    parser.add_argument("--with-source", action="store_true", help="vide aussi la base source PostgreSQL 13")
    args = parser.parse_args()

    # Une seule transaction : si une étape échoue, rien n'est supprimé
    with psycopg.connect(os.environ["WAREHOUSE_DSN"]) as conn:
        dropped = drop_partitions(conn)
        conn.execute("truncate " + ", ".join(f"raw.{t}" for t in PLAIN_TABLES))
        conn.execute("truncate obs.watermarks")  # la prochaine ingestion relit tout
        dbt_tables = conn.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema = any(%s) and table_type = 'BASE TABLE'",
            (list(DBT_SCHEMAS),),
        ).fetchall()
        if dbt_tables:
            conn.execute(
                sql.SQL("truncate {}").format(
                    sql.SQL(", ").join(sql.Identifier(schema, name) for schema, name in dbt_tables)
                )
            )
    print(f"Entrepôt : {dropped} partitions supprimées, tables raw et watermarks vidés")
    print(f"dbt : {len(dbt_tables)} tables vidées ({', '.join(DBT_SCHEMAS)}), reconstruites au prochain build")

    duckdb_file = pathlib.Path(os.environ.get("DUCKDB_PATH", "data/duckdb/mart.duckdb"))
    duckdb_file.unlink(missing_ok=True)  # le data mart sera republié
    print(f"DuckDB : {duckdb_file} supprimé")

    if args.with_source:
        with psycopg.connect(os.environ["SOURCE_DSN"]) as conn:
            # restart identity : les _src_id repartent à 1, comme une base neuve
            conn.execute("truncate " + ", ".join(SOURCE_TABLES) + " restart identity")
        print("Source : toutes les tables vidées")


if __name__ == "__main__":
    main()
