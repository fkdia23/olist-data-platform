#!/usr/bin/env bash
# srv-source/generator/generate.sh
# Génère les données dans la base source, de la fin de la dernière génération jusqu'à maintenant,
# puis applique le mode demandé :
#   normal   rien de plus
#   break    scénario 2 : 50 horodatages illisibles + 50 clients orphelins (rejetés par le contrat)
#   degrade  scénario 3 : +8 % d'articles conformes mais faux pour le métier (quarantaine dbt)
#   burst    scénario 4 : les 30 derniers jours rejoués à SCALE fois le volume, d'un coup
# Usage : generate.sh [SCALE] [normal|break|degrade|burst]
# Variables : SOURCE_DSN (obligatoire) ; WAREHOUSE_DSN et RUN_ID (optionnelles, pour les KPIs)
set -euo pipefail                  # stoppe à la 1re erreur ; variable non définie = erreur

SCALE="${1:-${SCALE:-1}}"          # 1er argument, sinon variable d'environnement, sinon 1
MODE="${2:-normal}"

if ! [[ "$SCALE" =~ ^[1-9][0-9]*$ ]]; then
  echo "SCALE doit être un entier positif (reçu : $SCALE)" >&2
  exit 2
fi
case "$MODE" in
  normal|break|degrade|burst) ;;
  *) echo "mode inconnu : $MODE (normal, break, degrade ou burst)" >&2; exit 2 ;;
esac

# -X : ignore ~/.psqlrc ; ON_ERROR_STOP : psql échoue si une requête échoue ; -t -A : sortie brute
PSQL=(psql "${SOURCE_DSN:?SOURCE_DSN doit être défini}" -X -q -v ON_ERROR_STOP=1 -t -A)

# Les fonctions du générateur sont rechargées à chaque appel (toutes en « create or replace ») :
# une modification de 02_generator.sql s'applique sans recréer le volume de pg13.
"${PSQL[@]}" -f "$(dirname "$0")/../db/init/02_generator.sql" >/dev/null

start=$(date +%s)
echo "Référentiels : $("${PSQL[@]}" -c "select olist_reference(${SCALE});")"
echo "Faits        : $("${PSQL[@]}" -c "select olist_generate_until_now(${SCALE});")"
case "$MODE" in
  break)   echo "Contrat cassé : $("${PSQL[@]}" -c "select olist_break_contract(50);")" ;;
  degrade) echo "Qualité dégradée : $("${PSQL[@]}" -c "select olist_degrade_quality(8);")" ;;
  burst)   echo "Surcharge : $("${PSQL[@]}" -c "select olist_burst(${SCALE}, 30);")" ;;
esac
duration=$(( $(date +%s) - start ))

"${PSQL[@]}" -c "analyze;"         # met à jour les statistiques (comptes de lignes ci-dessous)
echo "Durée : ${duration} s (SCALE=${SCALE}, mode=${MODE})"
"${PSQL[@]}" -F $'\t' -c "
  select relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid))
  from pg_stat_user_tables order by pg_total_relation_size(relid) desc;"
size_mb=$("${PSQL[@]}" -c "select round(pg_database_size(current_database()) / 1024.0 / 1024.0);")
echo "Taille de la base : ${size_mb} Mo"

# KPIs vers l'entrepôt, seulement si WAREHOUSE_DSN est défini et que obs existe (étape 7)
if [[ -n "${WAREHOUSE_DSN:-}" ]]; then
  rows_sql=$("${PSQL[@]}" -c "
    select string_agg(format('(nullif(%L, '''')::bigint, ''generate'', %L, %s)',
                             '${RUN_ID:-}', 'rows.' || relname, n_live_tup), ',')
    from pg_stat_user_tables where relname <> 'generation_runs';")
  psql "$WAREHOUSE_DSN" -X -q -v ON_ERROR_STOP=1 -c "
    insert into obs.run_metrics (run_id, step, metric, value) values
      (nullif('${RUN_ID:-}', '')::bigint, 'generate', 'duration_s', ${duration}),
      (nullif('${RUN_ID:-}', '')::bigint, 'generate', 'size_mb', ${size_mb}),
      ${rows_sql};" \
    && echo "KPIs enregistrés dans obs.run_metrics" \
    || echo "obs.run_metrics indisponible : KPIs non enregistrés (normal avant l'étape 7)"
fi
