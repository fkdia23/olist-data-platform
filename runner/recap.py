# runner/recap.py
"""E-mail récapitulatif envoyé à Mailpit à la fin de chaque run : étapes, volumétrie, qualité, liens.

Tout est relu dans obs (pipeline_runs, run_metrics, alerts) : l'e-mail montre exactement ce que
Streamlit et Grafana affichent. Un SMTP en panne ne fait jamais échouer un run.
"""

import os
import smtplib
from email.message import EmailMessage
from html import escape

from sqlalchemy import text

from runner.db import engine

LINKS = {
    "Streamlit": os.environ.get("STREAMLIT_PUBLIC_URL", "http://localhost:8501"),
    "Grafana": os.environ.get("GRAFANA_PUBLIC_URL", "http://localhost:3001/d/olist-pilotage"),
    "Marquez": os.environ.get("MARQUEZ_PUBLIC_URL", "http://localhost:3000"),
}


def _fmt(value: float | None, unit: str = "") -> str:
    if value is None:
        return "—"
    number = f"{value:,.0f}".replace(",", " ") if abs(value) >= 100 or value == int(value) else f"{value:.1f}"
    return f"{number}{unit}"


def _load(run_id: int):
    with engine.connect() as conn:
        run = conn.execute(
            text(
                "select run_id, scenario, trigger, status, error, requested_at, "
                "extract(epoch from finished_at - started_at) as duration_s "
                "from obs.pipeline_runs where run_id = :r"
            ),
            {"r": run_id},
        ).one()
        rows = conn.execute(
            text("select step, metric, value from obs.run_metrics where run_id = :r order by recorded_at"),
            {"r": run_id},
        ).all()
        alerts = conn.execute(
            text(
                "select alertname, severity, status from obs.alerts "
                "where received_at >= :t order by received_at"
            ),
            {"t": run.requested_at},
        ).all()
    metrics = {(step, metric): value for step, metric, value in rows}  # la dernière valeur l'emporte
    steps = [(step, value, metrics.get((step, "exit_code"))) for step, metric, value in rows
             if metric == "step_duration_s"]
    return run, metrics, steps, alerts


def _sections(metrics: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    m = metrics.get
    sections = []
    if m(("load_raw", "rows_read")) is not None:
        tables = sorted(k[0].split(":", 1)[1] for k in metrics if k[0].startswith("load_raw:") and k[1] == "rows_inserted")
        sections.append(("Ingestion (source → raw)", [
            ("Lignes lues", _fmt(m(("load_raw", "rows_read")))),
            ("Insérées", _fmt(m(("load_raw", "rows_inserted")))),
            ("Rejetées par le contrat", _fmt(m(("load_raw", "rows_rejected")))),
            ("Conformité", _fmt(m(("load_raw", "conformity_pct")), " %")),
            ("Débit", _fmt(m(("load_raw", "rows_per_s")), " lignes/s")),
            *[(f"  {t}", _fmt(m((f"load_raw:{t}", "rows_inserted")))) for t in tables],
        ]))
    if m(("dbt", "tests_pass")) is not None or m(("quality", "quarantine_pct")) is not None:
        sections.append(("Transformation et qualité (dbt)", [
            ("Modèles construits", _fmt(m(("dbt", "models_ok")))),
            ("Tests OK / avertis / en échec",
             f"{_fmt(m(('dbt', 'tests_pass')))} / {_fmt(m(('dbt', 'tests_warn')))} / {_fmt(m(('dbt', 'tests_fail')))}"),
            ("Articles contrôlés", _fmt(m(("quality", "rows_checked")))),
            ("En quarantaine", f"{_fmt(m(('quality', 'rows_quarantined')))} ({_fmt(m(('quality', 'quarantine_pct')), ' %')})"),
        ]))
    if m(("publish_duckdb", "size_bytes")) is not None:
        tables = sorted(k[1][5:] for k in metrics if k[0] == "publish_duckdb" and k[1].startswith("rows."))
        sections.append(("Data mart DuckDB", [
            ("Taille", _fmt(m(("publish_duckdb", "size_bytes")) / 1e6, " Mo")),
            *[(f"  {t}", _fmt(m(("publish_duckdb", f"rows.{t}")))) for t in tables],
        ]))
    if m(("stress", "pairs")) is not None:
        sections.append(("Charge (requête lourde sur pg16)", [
            ("Requêtes", _fmt(m(("stress", "queries")))),
            ("Paires calculées", _fmt(m(("stress", "pairs")))),
            ("Débordement disque", _fmt(m(("stress", "temp_mb")), " Mo")),
            ("Requête la plus lente", _fmt(m(("stress", "max_query_s")), " s")),
        ]))
    if m(("generate", "size_mb")) is not None:
        sections.append(("Source PostgreSQL 13", [("Taille de la base", _fmt(m(("generate", "size_mb")), " Mo"))]))
    sections.append(("Traçabilité OpenLineage", [
        ("Événements du runner", _fmt(m(("lineage", "events_runner")))),
        ("Événements de dbt-ol", _fmt(m(("lineage", "events_dbt")))),
    ]))
    return sections


def send_recap(run_id: int) -> None:
    try:
        run, metrics, steps, alerts = _load(run_id)
    except Exception as exc:
        print(f"recap: lecture impossible : {exc}", flush=True)
        return
    inserted = metrics.get(("load_raw", "rows_inserted"))
    duration = _fmt(run.duration_s, " s")
    subject = f"[{run.status.upper()}] run {run.run_id} · {run.scenario} · {duration}"
    if inserted is not None:
        subject += f" · {_fmt(inserted)} lignes insérées"
    sections = _sections(metrics)

    lines = [f"Scénario : {run.scenario} (déclencheur : {run.trigger})", f"Statut : {run.status}",
             f"Durée : {duration}"]
    if run.error:
        lines.append(f"Erreur : {run.error}")
    lines += ["", "Étapes :"] + [f"  {'OK ' if code == 0 else 'KO '} {name:<18} {_fmt(s, ' s')}"
                                  for name, s, code in steps]
    for title, rows in sections:
        lines += ["", f"{title} :"] + [f"  {label:<28} {value}" for label, value in rows]
    if alerts:
        lines += ["", "Alertes Grafana reçues pendant le run :"] + [
            f"  [{a.status}] {a.alertname} ({a.severity})" for a in alerts]
    lines += ["", *[f"{name} : {url}" for name, url in LINKS.items()]]

    color = {"success": "#1a7f37", "failed": "#cf222e"}.get(run.status, "#57606a")
    html = [f"<h2 style='color:{color}'>Run {run.run_id} · {escape(run.scenario)} · {escape(run.status)}</h2>",
            f"<p>Durée : <b>{duration}</b> · déclencheur : {escape(run.trigger)}</p>"]
    if run.error:
        html.append(f"<p style='color:#cf222e'><b>Erreur :</b> {escape(run.error)}</p>")
    html.append("<h3>Étapes</h3><table cellpadding='4' style='border-collapse:collapse'>")
    html += [f"<tr><td>{'✅' if code == 0 else '❌'}</td><td>{escape(name)}</td><td align='right'>{_fmt(s, ' s')}</td></tr>"
             for name, s, code in steps]
    html.append("</table>")
    for title, rows in sections:
        html.append(f"<h3>{escape(title)}</h3><table cellpadding='3' style='border-collapse:collapse'>")
        html += [f"<tr><td>{escape(label)}</td><td align='right'><b>{escape(value)}</b></td></tr>" for label, value in rows]
        html.append("</table>")
    if alerts:
        html.append("<h3>Alertes Grafana reçues pendant le run</h3><ul>")
        html += [f"<li>[{escape(a.status or '')}] {escape(a.alertname or '')} ({escape(a.severity or '')})</li>" for a in alerts]
        html.append("</ul>")
    html.append("<p>" + " · ".join(f"<a href='{url}'>{name}</a>" for name, url in LINKS.items()) + "</p>")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "runner@olist.test"
    msg["To"] = os.environ.get("RECAP_TO", "equipe@olist.test")
    msg.set_content("\n".join(lines))
    msg.add_alternative("\n".join(html), subtype="html")
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "mailpit"), int(os.environ.get("SMTP_PORT", "1025")),
                          timeout=10) as smtp:
            smtp.send_message(msg)
    except OSError as exc:  # un SMTP en panne ne doit pas faire échouer le run
        print(f"recap: {exc}", flush=True)
