#!/usr/bin/env bash
# scripts/lineage_build.sh : dbt build + émission OpenLineage, la même commande en local et en CI.
#   OPENLINEAGE_URL définie (poste de dev, Marquez démarré) -> événements envoyés à Marquez
#   OPENLINEAGE_URL absente (CI, sans Marquez)              -> événements écrits dans lineage/events.ndjson
# Usage : scripts/lineage_build.sh [options de dbt build]     ex. --select +fct_order_items
set -euo pipefail
cd "$(dirname "$0")/.."                        # racine du dépôt, d'où qu'on lance le script

# 1. Le .env, sans écraser ce qui est déjà exporté : en CI, les variables du job gardent la main
if [[ -f .env ]]; then
  while IFS='=' read -r nom valeur; do
    [[ "$nom" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue       # saute commentaires et lignes vides
    [[ -n "${!nom+x}" ]] || export "$nom=$valeur"
  done < .env
fi

# 2. Valeurs par défaut et choix du transport
export OPENLINEAGE_NAMESPACE="${OPENLINEAGE_NAMESPACE:-olist}"
if [[ -n "${OPENLINEAGE_URL:-}" ]]; then
  transport="http vers $OPENLINEAGE_URL"
else
  mkdir -p lineage
  export OPENLINEAGE__TRANSPORT__TYPE=file
  export OPENLINEAGE__TRANSPORT__LOG_FILE_PATH=lineage/events.ndjson
  export OPENLINEAGE__TRANSPORT__APPEND=true             # un seul fichier, complété à chaque run
  transport="fichier lineage/events.ndjson"
fi
echo "openlineage : namespace $OPENLINEAGE_NAMESPACE, transport $transport"

# 3. dbt build avec émission ; la sortie est gardée pour compter les événements
journal=$(mktemp)
trap 'rm -f "$journal"' EXIT
set +e
uv run dbt-ol build --project-dir dbt --profiles-dir dbt "$@" 2>&1 | tee "$journal"
code=${PIPESTATUS[0]}
set -e

# 4. Le KPI « statut d'émission » : 0 événement alors que dbt a tourné = traçabilité perdue
emis=$(grep -oE 'Emitted [0-9]+ OpenLineage events' "$journal" | grep -oE '[0-9]+' | tail -1 || true)
echo "openlineage : ${emis:-0} événement(s) émis, code de sortie de dbt : $code"
exit "$code"