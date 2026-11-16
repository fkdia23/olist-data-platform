# streamlit/app.py
"""Console de pilotage : lance un scénario, suit le run en direct, retrouve l'historique.

Streamlit ne fait que DÉPOSER une demande dans obs.pipeline_runs ; le runner l'exécute et écrit
logs et KPIs dans obs. Les mêmes chiffres alimentent Grafana et l'e-mail récapitulatif.
"""

import os
import re
from datetime import UTC, datetime

import pandas as pd
import streamlit as st
from sqlalchemy import text

st.set_page_config(page_title="Olist · pilotage", layout="wide")

# Hors Docker : localhost:5432 (port publié). Dans Docker : surcharger PG16_STREAMLIT_URL avec l'hôte pg16.
PG_URL = os.environ.get("PG16_STREAMLIT_URL",
                        "postgresql://warehouse:change_me_warehouse@localhost:5432/warehouse")
conn = st.connection("pg", type="sql", url=PG_URL)    # pool réutilisé d'une exécution à l'autre

# Liens ouverts par le navigateur : toujours localhost et les ports publiés
LINKS = {
    "Grafana · dashboard": os.environ.get("GRAFANA_PUBLIC_URL", "http://localhost:3001/d/olist-pilotage"),
    "Marquez · lineage": os.environ.get("MARQUEZ_PUBLIC_URL", "http://localhost:3000"),
    "Mailpit · e-mails": os.environ.get("MAILPIT_PUBLIC_URL", "http://localhost:8025"),
    "Prometheus": os.environ.get("PROMETHEUS_PUBLIC_URL", "http://localhost:9090"),
}

SCENARIOS = {
    "nominal": ("1 · Nominal", "Génération normale, ingestion, dbt build, publication DuckDB."),
    "contrat_casse": ("2 · Contrat cassé", "50 dates illisibles + 50 clients orphelins : rejetés, run en échec."),
    "qualite": ("3 · Qualité dégradée", "+8 % d'articles conformes mais faux : quarantaine dbt > 5 %."),
    "surcharge": ("4 · Surcharge", "30 jours rejoués à 40× le volume, puis requête lourde sur pg16."),
    "reset": ("5 · Réinitialisation", "Entrepôt, DuckDB et source à blanc, puis source propre régénérée."),
}
STATUS = {"success": ("🟢", "succès"), "failed": ("🔴", "échec"), "running": ("🔵", "en cours"),
          "queued": ("⚪", "en attente")}

# Un run sur une ligne : ses KPIs, pivotés depuis obs.run_metrics (step, metric, value)
KPIS = """
select p.run_id, p.scenario, p.trigger, p.status, p.error, p.requested_at,
       extract(epoch from coalesce(p.finished_at, now()) - p.started_at)            as duration_s,
       max(m.value) filter (where m.step = 'load_raw' and m.metric = 'rows_read')     as rows_read,
       max(m.value) filter (where m.step = 'load_raw' and m.metric = 'rows_inserted') as rows_inserted,
       max(m.value) filter (where m.step = 'load_raw' and m.metric = 'rows_rejected') as rows_rejected,
       max(m.value) filter (where m.step = 'load_raw' and m.metric = 'rows_per_s')    as rows_per_s,
       max(m.value) filter (where m.step = 'quality'  and m.metric = 'quarantine_pct') as quarantine_pct,
       max(m.value) filter (where m.step = 'dbt'      and m.metric = 'tests_warn')     as tests_warn,
       max(m.value) filter (where m.step = 'dbt'      and m.metric = 'tests_fail')     as tests_fail,
       max(m.value) filter (where m.step = 'publish_duckdb' and m.metric = 'size_bytes') as duckdb_bytes,
       max(m.value) filter (where m.step = 'stress'   and m.metric = 'temp_mb')        as temp_mb,
       max(m.value) filter (where m.step = 'lineage'  and m.metric = 'events_runner')  as ol_runner,
       max(m.value) filter (where m.step = 'lineage'  and m.metric = 'events_dbt')     as ol_dbt
from obs.pipeline_runs p
left join obs.run_metrics m using (run_id)
{where}
group by p.run_id
order by p.run_id desc
limit {limit}
"""


def num(value, unit="", decimals=0) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ") + unit


with st.sidebar:
    st.header("Outils")
    for label, url in LINKS.items():
        st.link_button(label, url)
    st.caption("Mêmes chiffres partout : obs.run_metrics alimente cette console, Grafana et l'e-mail récap.")

st.title("Pilotage de la plateforme Olist")
st.caption(f"Fenêtre de données : 2025-01-01 → {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")

# Les 5 boutons : chacun dépose une demande, le runner fait le reste
for col, (key, (label, help_text)) in zip(st.columns(5), SCENARIOS.items()):
    if col.button(label, key=f"btn-{key}", help=help_text, width="stretch"):
        with conn.session as s:                       # session SQLAlchemy fournie par st.connection
            s.execute(text("insert into obs.pipeline_runs (scenario, trigger) values (:s, 'streamlit')"),
                      {"s": key})
            s.commit()
        st.toast(f"{label} : demande envoyée au runner")


@st.fragment(run_every="2s")                          # seule cette zone se rafraîchit, toutes les 2 s
def live_panel():
    runs = conn.query(KPIS.format(where="", limit=1), ttl=0)   # ttl=0 : jamais de cache
    if runs.empty:
        st.info("Aucun run pour l'instant.")
        return
    run = runs.iloc[0]
    icon, status = STATUS.get(run.status, ("⚪", run.status))
    label = SCENARIOS.get(run.scenario, (run.scenario,))[0]
    st.subheader(f"{icon} Run {run.run_id} · {label} · {status}")
    if run.error:
        st.error(run.error)

    cols = st.columns(7)
    cols[0].metric("Durée", num(run.duration_s, " s"))
    cols[1].metric("Lues", num(run.rows_read))
    cols[2].metric("Insérées", num(run.rows_inserted))
    cols[3].metric("Rejetées", num(run.rows_rejected))
    cols[4].metric("Quarantaine", num(run.quarantine_pct, " %", 2))
    cols[5].metric("DuckDB", num(run.duckdb_bytes / 1e6 if run.duckdb_bytes else None, " Mo", 1))
    ol = None if pd.isna(run.ol_runner) else (run.ol_runner or 0) + (run.ol_dbt or 0)
    cols[6].metric("Évts OpenLineage", num(ol))

    left, right = st.columns([3, 2])
    with left:
        logs = conn.query("select message from obs.run_logs where run_id = :r "
                          "order by logged_at desc limit 200", params={"r": int(run.run_id)}, ttl=0)
        with st.container(height=470):               # hauteur fixe : la page ne s'allonge pas avec les logs
            st.code("\n".join(reversed(logs["message"].tolist())) or "…", language="text")  # dernières en bas
    with right:
        steps = conn.query("""
            select step as "Étape", round(max(value) filter (where metric = 'step_duration_s')) as "Durée (s)",
                   max(value) filter (where metric = 'exit_code') as "Code"
            from obs.run_metrics where run_id = :r and metric in ('step_duration_s', 'exit_code')
            group by step order by min(recorded_at)""", params={"r": int(run.run_id)}, ttl=0)
        st.markdown("**Étapes**")
        st.dataframe(steps, hide_index=True, width="stretch")
        alerts = conn.query("""
            select case status when 'firing' then '🔴 déclenchée' when 'resolved' then '🟢 résolue'
                               else status end                                  as "État",
                   alertname                                                    as "Alerte",
                   severity                                                     as "Sévérité",
                   to_char(received_at at time zone 'Europe/Paris', 'HH24:MI:SS') as "Reçue à"
            from obs.alerts where received_at >= :t order by alert_id desc limit 20""",
                            params={"t": run.requested_at}, ttl=0)
        st.markdown("**Alertes Grafana reçues depuis le début du run**")
        if alerts.empty:
            st.caption("Aucune pour l'instant (évaluation toutes les 20 s, envoi groupé après 30 s).")
        else:
            st.dataframe(alerts, hide_index=True, width="stretch")
        with st.expander("Tous les KPIs du run"):
            st.dataframe(conn.query("select step, metric, value from obs.run_metrics where run_id = :r "
                                    "order by recorded_at", params={"r": int(run.run_id)}, ttl=0),
                         hide_index=True, width="stretch")


@st.fragment(run_every="10s")
def history():
    st.subheader("Historique des runs")
    runs = conn.query(KPIS.format(where="", limit=15), ttl=0)
    if runs.empty:
        return
    def col(values, unit="", decimals=0):              # « — » quand la mesure est sans objet pour ce scénario
        return [num(v, unit, decimals) for v in values]

    table = pd.DataFrame({
        "Run": runs.run_id,
        "Scénario": [SCENARIOS.get(s, (s,))[0] for s in runs.scenario],
        "Statut": [" ".join(STATUS.get(s, ("", s))) for s in runs.status],
        "Durée": col(runs.duration_s, " s"),
        "Lues": col(runs.rows_read),
        "Insérées": col(runs.rows_inserted),
        "Rejetées": col(runs.rows_rejected),
        "Lignes/s": col(runs.rows_per_s),
        "Quarantaine": col(runs.quarantine_pct, " %", 2),
        "Tests avertis": col(runs.tests_warn),
        "DuckDB": col(runs.duckdb_bytes / 1e6, " Mo", 1),
        "Temp": col(runs.temp_mb, " Mo"),
        "OpenLineage": col(runs.ol_runner.fillna(0) + runs.ol_dbt.fillna(0)),
    })
    st.dataframe(table, hide_index=True, width="stretch")


CRON_FIELD = r"[0-9*/,\-]+"


@st.fragment(run_every="10s")
def crons():
    st.subheader("Crons")
    st.caption("Le runner relit obs.schedules toutes les 30 s : un cron ajouté ici part sans redémarrage "
               "(heure de Paris). Prochain passage et retard : dashboard Grafana, section Crons.")
    schedules = conn.query("""
        select s.schedule_id as "Cron", s.cron as "Expression", s.scenario as "Scénario",
               s.enabled as "Actif",
               count(p.run_id)                                         as "Runs",
               to_char(max(p.finished_at) filter (where p.status = 'success') at time zone 'Europe/Paris',
                       'DD/MM HH24:MI:SS')                             as "Dernier succès",
               (array_agg(p.status order by p.run_id desc))[1]         as "Dernier statut"
        from obs.schedules s
        left join obs.pipeline_runs p on p.schedule_id = s.schedule_id
        group by s.schedule_id order by s.schedule_id""", ttl=0)
    if schedules.empty:
        st.info("Aucun cron. Exemple : nominal toutes les 2 minutes, « */2 * * * * ».")
    else:
        schedules["Dernier statut"] = [" ".join(STATUS.get(s, ("", s or "—"))) for s in schedules["Dernier statut"]]
        st.dataframe(schedules, hide_index=True, width="stretch")

    add, toggle = st.columns([3, 2])
    with add.form("new-cron", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        schedule_id = c1.text_input("Identifiant", placeholder="nominal-2min")
        expression = c2.text_input("Expression (5 champs)", value="*/2 * * * *")
        scenario = c3.selectbox("Scénario", list(SCENARIOS), format_func=lambda k: SCENARIOS[k][0])
        if st.form_submit_button("Ajouter ou mettre à jour"):
            fields = expression.split()
            if not schedule_id.strip():
                st.error("Identifiant obligatoire.")
            elif len(fields) != 5 or not all(re.fullmatch(CRON_FIELD, f) for f in fields):
                st.error("Expression cron attendue sur 5 champs : minute heure jour mois jour-de-semaine.")
            else:
                with conn.session as s:
                    s.execute(text("""
                        insert into obs.schedules (schedule_id, cron, scenario) values (:i, :c, :s)
                        on conflict (schedule_id) do update
                        set cron = excluded.cron, scenario = excluded.scenario, enabled = true, updated_at = now()"""),
                              {"i": schedule_id.strip(), "c": " ".join(fields), "s": scenario})
                    s.commit()
                st.toast(f"Cron {schedule_id} enregistré : pris en compte par le runner sous 30 s")
    if not schedules.empty:
        with toggle.form("toggle-cron"):
            target = st.selectbox("Cron", schedules["Cron"].tolist())
            on, off = st.columns(2)
            enable = on.form_submit_button("Activer")
            disable = off.form_submit_button("Désactiver")
            if enable or disable:
                with conn.session as s:
                    s.execute(text("update obs.schedules set enabled = :e, updated_at = now() where schedule_id = :i"),
                              {"e": bool(enable), "i": target})
                    s.commit()
                st.toast(f"Cron {target} {'activé' if enable else 'désactivé'}")


live_panel()
history()
crons()
