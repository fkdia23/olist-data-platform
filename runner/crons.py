# runner/crons.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from prometheus_client import Counter, Gauge
from sqlalchemy import text

from runner.db import engine

TZ = "Europe/Paris"
scheduler = BackgroundScheduler(timezone=TZ)          # tourne dans un thread du runner

# Métriques de surveillance, une série par cron (label schedule_id)
CRON_RUNS = Counter("cron_runs_total", "Exécutions de cron par statut", ["schedule_id", "status"])
CRON_LAST_SUCCESS = Gauge("cron_last_success_timestamp_seconds", "Heure du dernier succès", ["schedule_id"])
CRON_LAST_DURATION = Gauge("cron_last_duration_seconds", "Durée de la dernière exécution", ["schedule_id"])
CRON_INTERVAL = Gauge("cron_expected_interval_seconds", "Écart prévu entre deux déclenchements", ["schedule_id"])
CRON_NEXT = Gauge("cron_next_fire_timestamp_seconds", "Prochain déclenchement prévu", ["schedule_id"])
_seen = set()                                          # crons dont l'heure de succès est déjà connue


def enqueue(schedule_id: str, scenario: str) -> None:
    """Appelée par APScheduler à l'heure dite : dépose une demande, le worker l'exécute."""
    with engine.begin() as conn:
        conn.execute(text("insert into obs.pipeline_runs (scenario, trigger, schedule_id) "
                          "values (:s, 'cron', :id)"), {"s": scenario, "id": schedule_id})


def on_cron_finished(schedule_id, status, duration) -> None:
    """Appelée par le runner à la fin d'un run."""
    if not schedule_id:
        return                                         # run lancé depuis Streamlit : rien à faire
    CRON_RUNS.labels(schedule_id, status).inc()
    CRON_LAST_DURATION.labels(schedule_id).set(duration)
    if status == "success":
        CRON_LAST_SUCCESS.labels(schedule_id).set_to_current_time()
        _seen.add(schedule_id)


def _forget(schedule_id: str) -> None:
    """Efface les séries d'un cron supprimé, sinon il déclencherait l'alerte de retard."""
    for gauge in (CRON_LAST_SUCCESS, CRON_INTERVAL, CRON_NEXT):
        try:
            gauge.remove(schedule_id)
        except KeyError:
            pass
    _seen.discard(schedule_id)


def sync_schedules() -> None:
    """Toutes les 30 s : aligne APScheduler sur la table obs.schedules."""
    with engine.connect() as conn:
        rows = conn.execute(text("select schedule_id, cron, scenario from obs.schedules where enabled")).all()
    wanted = {r.schedule_id: r for r in rows}

    for job in scheduler.get_jobs():                   # 1. retirer les crons supprimés ou désactivés
        sid = job.id.removeprefix("cron:")
        if job.id.startswith("cron:") and sid not in wanted:
            job.remove()
            _forget(sid)

    now = datetime.now(ZoneInfo(TZ))
    for sid, r in wanted.items():                      # 2. ajouter ou mettre à jour
        trigger = CronTrigger.from_crontab(r.cron, timezone=TZ)
        job = scheduler.get_job(f"cron:{sid}")
        if not job or job.name != f"{r.cron}|{r.scenario}":
            scheduler.add_job(
                enqueue, trigger, args=[sid, r.scenario],
                id=f"cron:{sid}",                      # identifiant stable : pas de doublon
                name=f"{r.cron}|{r.scenario}",         # permet de détecter une modification
                replace_existing=True,                 # remplace l'ancienne version
                max_instances=1,                       # jamais deux déclenchements en parallèle
                coalesce=True,                         # plusieurs créneaux manqués = une seule exécution
                misfire_grace_time=60,                 # au-delà de 60 s de retard : « missed »
            )
        if sid not in _seen:                           # cron neuf : le compte à rebours part de maintenant
            CRON_LAST_SUCCESS.labels(sid).set_to_current_time()
            _seen.add(sid)
        first = trigger.get_next_fire_time(None, now)  # 3. publier le prochain passage et l'intervalle
        second = trigger.get_next_fire_time(first, first + timedelta(seconds=1))
        CRON_NEXT.labels(sid).set(first.timestamp())
        CRON_INTERVAL.labels(sid).set((second - first).total_seconds())


def _restore_last_success() -> None:
    """Au démarrage, recharge le dernier succès depuis obs : sinon l'alerte partirait à tort."""
    with engine.connect() as conn:
        rows = conn.execute(text("select schedule_id, extract(epoch from max(finished_at)) as ts "
                                 "from obs.pipeline_runs where trigger = 'cron' and status = 'success' "
                                 "group by schedule_id")).all()
    for r in rows:
        CRON_LAST_SUCCESS.labels(r.schedule_id).set(float(r.ts))
        _seen.add(r.schedule_id)


def _on_event(event) -> None:
    """Déclenchement manqué (runner arrêté, trop de retard) ou erreur lors du dépôt."""
    if event.job_id.startswith("cron:"):
        status = "missed" if event.code == EVENT_JOB_MISSED else "error"
        CRON_RUNS.labels(event.job_id.removeprefix("cron:"), status).inc()


def start_scheduler() -> None:
    _restore_last_success()
    scheduler.add_listener(_on_event, EVENT_JOB_MISSED | EVENT_JOB_ERROR)
    scheduler.add_job(sync_schedules, "interval", seconds=30, id="sync-schedules",
                      replace_existing=True, max_instances=1,
                      next_run_time=datetime.now(ZoneInfo(TZ)))   # première synchro immédiate
    scheduler.start()


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)