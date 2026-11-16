# runner/app.py
"""Runner : exécute, un à la fois, les scénarios déposés dans obs.pipeline_runs.

Pour chaque run :
  - étapes lancées dans l'ordre, arrêt au premier échec ; la sortie va dans obs.run_logs ;
  - KPIs dans obs.run_metrics (les outils écrivent les leurs grâce à RUN_ID, le runner ajoute
    durées, résultats dbt, quarantaine, événements OpenLineage) ;
  - métriques Prometheus sur /metrics (Grafana en tire ses alertes) ;
  - traçabilité OpenLineage : un run par scénario, un run enfant par étape (voir runner/lineage.py) ;
  - récapitulatif par e-mail vers Mailpit (runner/recap.py).
"""

import json
import os
import pathlib
import re
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from sqlalchemy import text

from runner import lineage
from runner.crons import on_cron_finished, start_scheduler, stop_scheduler  # partie optionnelle
from runner.db import engine
from runner.recap import send_recap

SOURCE_TABLES = ("geolocation", "customers", "sellers", "products",
                 "orders", "order_items", "order_payments", "order_reviews")
FACT_TABLES = ("customers", "orders", "order_items", "order_payments", "order_reviews")
MARTS = ("dim_date", "fct_order_items")


@dataclass(frozen=True)
class Step:
    name: str                                   # nom court : step des KPIs, job OpenLineage, console
    cmd: tuple[str, ...]                        # lancé depuis la racine du dépôt
    inputs: tuple[lineage.Dataset, ...] = ()    # datasets lus (pour OpenLineage)
    outputs: tuple[lineage.Dataset, ...] = ()   # datasets écrits
    native_lineage: bool = False                # True : l'outil émet lui-même (dbt-ol), on le rattache


def generate(mode: str, scale: str = "1", outputs: tuple[str, ...] = FACT_TABLES) -> Step:
    return Step(f"generate_{mode}", ("bash", "srv-source/generator/generate.sh", scale, mode),
                outputs=lineage.source(outputs))


LOAD_RAW = Step("load_raw", ("python", "-m", "ingestion.load_raw"),
                inputs=lineage.source(SOURCE_TABLES), outputs=lineage.warehouse("raw", SOURCE_TABLES))
# --fail-on-reject : code 3 dès qu'une ligne est rejetée, donc le run passe en échec
LOAD_RAW_STRICT = Step("load_raw", (*LOAD_RAW.cmd, "--fail-on-reject"), LOAD_RAW.inputs, LOAD_RAW.outputs)
DBT_BUILD = Step("dbt", ("dbt-ol", "build", "--project-dir", "dbt", "--profiles-dir", "dbt"),
                 native_lineage=True)
PUBLISH = Step("publish_duckdb", ("python", "-m", "mart.publish_duckdb"),
               inputs=lineage.warehouse("marts", MARTS), outputs=lineage.duckdb(MARTS))
STRESS = Step("stress", ("python", "-m", "stress.geolocation_join"),
              inputs=lineage.warehouse("raw", ("geolocation",)))
RESET = Step("reset", ("python", "-m", "ingestion.reset", "--with-source"))

SCENARIOS: dict[str, list[Step]] = {
    # 1. La journée type : le fournisseur produit, on ingère, on transforme, on publie
    "nominal":       [generate("normal", outputs=SOURCE_TABLES), LOAD_RAW, DBT_BUILD, PUBLISH],
    # 2. 50 dates illisibles + 50 clients orphelins : rejetés, le run échoue (alertes)
    "contrat_casse": [generate("break", outputs=("orders",)), LOAD_RAW_STRICT],
    # 3. +8 % d'articles conformes mais faux : acceptés, mis en quarantaine par dbt (alerte > 5 %)
    "qualite":       [generate("degrade", outputs=("order_items",)), LOAD_RAW, DBT_BUILD, PUBLISH],
    # 4. 30 jours rejoués à 40 fois le volume, puis une requête lourde sur pg16
    "surcharge":     [generate("burst", "40"), LOAD_RAW, STRESS],
    # 5. Tout à blanc (entrepôt, DuckDB ET source), puis une source propre régénérée
    "reset":         [RESET, generate("normal", outputs=SOURCE_TABLES)],
}

# ---------- Métriques Prometheus (lues sur runner:8000/metrics) ----------
RUNS = Counter("runner_runs_total", "Runs terminés", ["scenario", "status"])
DURATION = Histogram("runner_run_duration_seconds", "Durée des runs", ["scenario"],
                     buckets=(10, 30, 60, 120, 300, 600, 1800))
STEP_DURATION = Gauge("runner_step_duration_seconds", "Durée de la dernière exécution d'une étape",
                      ["scenario", "step"])
LAST_RUN = Gauge("runner_last_run_timestamp_seconds", "Fin du dernier run", ["scenario", "status"])
ROWS = Counter("pipeline_rows_total", "Lignes vues par l'ingestion", ["kind"])      # read, inserted, rejected
QUARANTINE = Gauge("pipeline_quarantine_ratio", "Part des articles en quarantaine (dernier dbt build)")
DUCKDB_SIZE = Gauge("mart_duckdb_size_bytes", "Taille du fichier mart.duckdb publié")
LINEAGE_EVENTS = Counter("lineage_events_total", "Événements OpenLineage émis", ["emitter"])  # runner, dbt

# Séries créées à 0 dès le démarrage : sans elles, increase() ne verrait pas le PREMIER échec
for _scenario in SCENARIOS:
    for _status in ("success", "failed"):
        RUNS.labels(_scenario, _status)
for _kind in ("read", "inserted", "rejected"):
    ROWS.labels(_kind)
for _emitter in ("runner", "dbt"):
    LINEAGE_EVENTS.labels(_emitter)

# Réserve la plus ancienne demande en attente. SKIP LOCKED : une demande n'est prise qu'une fois,
# même si deux runners tournaient en même temps.
CLAIM = text("""
    update obs.pipeline_runs set status = 'running', started_at = now()
    where run_id = (select run_id from obs.pipeline_runs
                    where status = 'queued'
                    order by requested_at
                    limit 1
                    for update skip locked)
    returning run_id, scenario, schedule_id
""")
DBT_EMITTED = re.compile(r"Emitted (\d+) OpenLineage events")
DBT_RUN_RESULTS = pathlib.Path("dbt/target/run_results.json")


def log(run_id: int, message: str) -> None:
    """Écrit une ligne de log ; la console Streamlit la lit."""
    with engine.begin() as conn:
        conn.execute(text("insert into obs.run_logs (run_id, message) values (:r, :m)"),
                     {"r": run_id, "m": message})


def record(run_id: int, step: str, values: dict[str, float]) -> None:
    """KPIs calculés par le runner, rangés comme ceux des outils : (run_id, step, metric, value)."""
    with engine.begin() as conn:
        conn.execute(text("insert into obs.run_metrics (run_id, step, metric, value) values (:r, :s, :m, :v)"),
                     [{"r": run_id, "s": step, "m": k, "v": float(v)} for k, v in values.items()])


def metrics_of(run_id: int, step: str) -> dict[str, float]:
    with engine.connect() as conn:
        rows = conn.execute(text("select metric, value from obs.run_metrics where run_id = :r and step = :s "
                                 "order by recorded_at"), {"r": run_id, "s": step}).all()
    return {metric: value for metric, value in rows}


def measure_quarantine(run_id: int) -> None:
    """Part des articles en quarantaine, mesurée sur la table que dbt vient de construire."""
    with engine.connect() as conn:
        total, bad = conn.execute(text(
            "select count(*), count(*) filter (where not is_valid) "
            "from intermediate.int_order_items__checked")).one()
    ratio = bad / total if total else 0.0
    QUARANTINE.set(ratio)
    record(run_id, "quality", {"rows_checked": total, "rows_quarantined": bad,
                               "quarantine_pct": round(100 * ratio, 2)})


def read_dbt_results(run_id: int) -> None:
    """Bilan de dbt build lu dans target/run_results.json : modèles construits, tests par statut."""
    try:
        results = json.loads(DBT_RUN_RESULTS.read_text())["results"]
    except (OSError, ValueError, KeyError):
        return
    tests = [r["status"] for r in results if r["unique_id"].startswith("test.")]
    models = [r["status"] for r in results if r["unique_id"].startswith("model.")]
    record(run_id, "dbt", {
        "models_ok": models.count("success"), "models_error": models.count("error"),
        "tests_pass": tests.count("pass"), "tests_warn": tests.count("warn"),
        "tests_fail": tests.count("fail") + tests.count("error"),
    })


def after_step(run_id: int, step: Step, code: int) -> dict[str, int]:
    """KPIs dérivés d'une étape ; renvoie les lignes écrites par dataset (pour OpenLineage)."""
    row_counts: dict[str, int] = {}
    try:
        if step.name == "load_raw":
            m = metrics_of(run_id, "load_raw")
            for kind in ("read", "inserted", "rejected"):
                ROWS.labels(kind).inc(m.get(f"rows_{kind}", 0))
            for ns, name in step.outputs:            # warehouse.raw.orders <- load_raw:orders
                table = name.rsplit(".", 1)[1]
                inserted = metrics_of(run_id, f"load_raw:{table}").get("rows_inserted")
                if inserted is not None:
                    row_counts[name] = int(inserted)
        elif step.name == "dbt":
            read_dbt_results(run_id)
            measure_quarantine(run_id)               # même si un test échoue : la table existe
        elif step.name == "publish_duckdb" and code == 0:
            m = metrics_of(run_id, "publish_duckdb")
            DUCKDB_SIZE.set(m.get("size_bytes", 0))
            for ns, name in step.outputs:            # mart.main.fct_order_items <- rows.fct_order_items
                table = name.rsplit(".", 1)[1]
                if f"rows.{table}" in m:
                    row_counts[name] = int(m[f"rows.{table}"])
        elif step.name == "reset" and code == 0:
            QUARANTINE.set(0)                        # plus de données : plus de quarantaine
            DUCKDB_SIZE.set(0)                       # le fichier a été supprimé
            record(run_id, "quality", {"quarantine_pct": 0})
            record(run_id, "publish_duckdb", {"size_bytes": 0})
    except Exception as exc:                          # un KPI manquant ne doit pas casser le run
        log(run_id, f"[runner] KPIs de {step.name} incomplets : {exc}")
    return row_counts


def run_step(run_id: int, step: Step, env: dict[str, str]) -> tuple[int, float, int]:
    """Lance une étape ; renvoie (code de sortie, durée, événements OpenLineage émis par l'outil)."""
    log(run_id, "$ " + " ".join(step.cmd))           # la commande apparaît dans la console
    started = time.monotonic()
    emitted = 0
    proc = subprocess.Popen(step.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    for line in proc.stdout:                         # sortie lue au fil de l'eau
        line = line.rstrip()
        if match := DBT_EMITTED.search(line):
            emitted += int(match.group(1))
        log(run_id, line)
    return proc.wait(), time.monotonic() - started, emitted


def execute(run_id: int, scenario: str, schedule_id: str | None) -> None:
    """Lance les étapes, arrête au premier échec, enregistre le résultat."""
    started = time.monotonic()
    status, error = "success", None
    scenario_job, scenario_run = f"scenario.{scenario}", lineage.new_run_id()
    parent = (scenario_job, scenario_run)
    events_runner = lineage.emit(lineage.START, scenario_job, scenario_run, job_type="JOB")
    events_dbt = 0
    log(run_id, f"[runner] OpenLineage : job {lineage.NAMESPACE}/{scenario_job}, run {scenario_run}")
    env = {**os.environ, "RUN_ID": str(run_id)}      # load_raw, generate.sh… rattachent leurs KPIs au run

    for step in SCENARIOS[scenario]:
        step_run = lineage.new_run_id()
        step_env = dict(env)
        if step.native_lineage:                       # dbt-ol : son run devient l'enfant du scénario
            step_env["OPENLINEAGE_PARENT_ID"] = f"{lineage.NAMESPACE}/{scenario_job}/{scenario_run}"
        else:
            events_runner += lineage.emit(lineage.START, step.name, step_run, parent=parent,
                                          inputs=step.inputs, outputs=step.outputs)
        code, step_s, emitted = run_step(run_id, step, step_env)
        events_dbt += emitted
        record(run_id, step.name, {"step_duration_s": step_s, "exit_code": code})
        STEP_DURATION.labels(scenario, step.name).set(step_s)
        row_counts = after_step(run_id, step, code)
        if code != 0:
            status, error = "failed", f"échec de l'étape {step.name} (code {code}) : {' '.join(step.cmd)}"
        if not step.native_lineage:
            events_runner += lineage.emit(lineage.COMPLETE if code == 0 else lineage.FAIL, step.name, step_run,
                                          parent=parent, inputs=step.inputs, outputs=step.outputs,
                                          row_counts=row_counts, error=error if code else None)
        if code != 0:
            break                                     # les étapes suivantes ne sont pas lancées

    duration = time.monotonic() - started
    events_runner += lineage.emit(lineage.COMPLETE if status == "success" else lineage.FAIL,
                                  scenario_job, scenario_run, error=error, job_type="JOB")
    LINEAGE_EVENTS.labels("runner").inc(events_runner)
    LINEAGE_EVENTS.labels("dbt").inc(events_dbt)
    record(run_id, "lineage", {"events_runner": events_runner, "events_dbt": events_dbt})
    record(run_id, "runner", {"duration_s": duration})
    with engine.begin() as conn:                      # le statut en dernier : les KPIs sont déjà là
        conn.execute(text("update obs.pipeline_runs set status = :s, finished_at = now(), error = :e "
                          "where run_id = :r"), {"s": status, "e": error, "r": run_id})
    RUNS.labels(scenario, status).inc()
    DURATION.labels(scenario).observe(duration)
    LAST_RUN.labels(scenario, status).set_to_current_time()
    on_cron_finished(schedule_id, status, duration)   # sans effet si le run ne vient pas d'un cron
    send_recap(run_id)


def worker_loop() -> None:
    """Boucle infinie : prend une demande, l'exécute, recommence."""
    while True:
        try:
            with engine.begin() as conn:              # transaction courte : on réserve, puis on relâche
                row = conn.execute(CLAIM).first()
            if row:
                execute(row.run_id, row.scenario, row.schedule_id)
            else:
                time.sleep(2)                         # rien en attente
        except Exception as exc:                      # une panne de PostgreSQL ne doit pas tuer la boucle
            print(f"worker: {exc}", flush=True)
            time.sleep(5)


def restore_gauges() -> None:
    """Au démarrage, recharge la dernière quarantaine et la taille DuckDB : les alertes restent justes."""
    with engine.connect() as conn:
        for gauge, step, metric, factor in ((QUARANTINE, "quality", "quarantine_pct", 0.01),
                                            (DUCKDB_SIZE, "publish_duckdb", "size_bytes", 1)):
            value = conn.execute(text("select value from obs.run_metrics where step = :s and metric = :m "
                                      "order by recorded_at desc limit 1"), {"s": step, "m": metric}).scalar()
            gauge.set((value or 0) * factor)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.begin() as conn:                      # runs coupés par un redémarrage : marqués en échec
        conn.execute(text("update obs.pipeline_runs set status = 'failed', "
                          "error = 'runner redémarré' where status = 'running'"))
    restore_gauges()
    threading.Thread(target=worker_loop, daemon=True).start()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(lifespan=lifespan)
app.mount("/metrics", make_asgi_app())                # Prometheus lit runner:8000/metrics


@app.post("/grafana-alerts")
async def grafana_alerts(request: Request):
    """Webhook appelé par Grafana : chaque alerte devient une ligne de obs.alerts."""
    body = await request.json()
    alerts = body.get("alerts", [])                   # une notification peut regrouper plusieurs alertes
    with engine.begin() as conn:
        for alert in alerts:
            labels = alert.get("labels", {})
            conn.execute(text("insert into obs.alerts (alertname, severity, status, payload) "
                              "values (:n, :s, :st, cast(:p as jsonb))"),
                         {"n": labels.get("alertname"), "s": labels.get("severity", "info"),
                          "st": alert.get("status"), "p": json.dumps(alert)})
    return {"stored": len(alerts)}
