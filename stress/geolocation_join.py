# stress/geolocation_join.py
"""Scénario 4 : met pg16 sous tension avec une requête volontairement lourde.

uv run --env-file .env python -m stress.geolocation_join

Auto-jointure de raw.geolocation sur le préfixe postal : environ 10 points par préfixe, donc
une dizaine de millions de paires pour 1 million de lignes. Elle tourne sur STRESS_WORKERS
connexions en parallèle, STRESS_ROUNDS fois chacune, avec un work_mem volontairement petit :
les hachages débordent sur disque (temp_bytes). cAdvisor (CPU de pg16), postgres_exporter
(fichiers temporaires) et Grafana le montrent. KPIs dans obs.run_metrics, step stress.
Variables : WAREHOUSE_DSN (obligatoire), STRESS_WORKERS (4), STRESS_ROUNDS (2), RUN_ID.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg

QUERY = """
    select g1.geolocation_state,
           count(*)                                                as pairs,
           avg(abs(g1.geolocation_lat - g2.geolocation_lat)
               + abs(g1.geolocation_lng - g2.geolocation_lng))     as spread
    from raw.geolocation g1
    join raw.geolocation g2 using (geolocation_zip_code_prefix)
    group by g1.geolocation_state
"""
TEMP_BYTES = "select temp_bytes from pg_stat_database where datname = current_database()"


def one_worker(dsn: str, worker: int, rounds: int) -> tuple[int, float]:
    pairs, slowest = 0, 0.0
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("set work_mem = '2MB'")              # petit exprès : force le débordement disque
        conn.execute("set statement_timeout = '300s'")    # garde-fou : jamais plus de 5 minutes
        for i in range(rounds):
            t0 = time.monotonic()
            rows = conn.execute(QUERY).fetchall()
            elapsed = time.monotonic() - t0
            slowest = max(slowest, elapsed)
            pairs += sum(r[1] for r in rows)
            print(f"connexion {worker} · requête {i + 1}/{rounds} : {sum(r[1] for r in rows):,} paires "
                  f"en {elapsed:.1f} s".replace(",", " "), flush=True)
    return pairs, slowest


def main() -> None:
    dsn = os.environ["WAREHOUSE_DSN"]
    workers = int(os.environ.get("STRESS_WORKERS", "4"))
    rounds = int(os.environ.get("STRESS_ROUNDS", "2"))
    with psycopg.connect(dsn) as conn:
        geo_rows = conn.execute("select count(*) from raw.geolocation").fetchone()[0]
        temp_before = conn.execute(TEMP_BYTES).fetchone()[0]
    if not geo_rows:
        raise SystemExit("raw.geolocation est vide : lance d'abord l'ingestion")
    print(f"{geo_rows:,} points de géolocalisation · {workers} connexions × {rounds} requêtes".replace(",", " "),
          flush=True)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda w: one_worker(dsn, w + 1, rounds), range(workers)))
    duration = time.monotonic() - started

    with psycopg.connect(dsn) as conn:
        temp_mb = (conn.execute(TEMP_BYTES).fetchone()[0] - temp_before) / 1e6
        values = {"duration_s": duration, "queries": workers * rounds, "pairs": sum(p for p, _ in results),
                  "max_query_s": max(s for _, s in results), "temp_mb": round(temp_mb, 1)}
        if os.environ.get("RUN_ID"):
            conn.cursor().executemany(
                "insert into obs.run_metrics (run_id, step, metric, value) values (%s, 'stress', %s, %s)",
                [(int(os.environ["RUN_ID"]), k, float(v)) for k, v in values.items()],
            )
    print(f"Charge terminée en {duration:.1f} s : {values['pairs']:,} paires, "
          f"{temp_mb:.0f} Mo écrits en fichiers temporaires".replace(",", " "))


if __name__ == "__main__":
    main()
