# Raccourcis du projet : make <cible>. Chaque cible lit le fichier .env.
SHELL := /bin/bash
UV := uv run --env-file .env
LOAD_ENV := set -a && source .env && set +a
SCALE ?= 1

.PHONY: up down ps migrate generate generate-break ingest ingest-full reset test test-integration obs-roles obs-up pilotage-up lint check marquez-db lineage-up lineage-check lineage-build

up:              ## démarre les conteneurs (profils de COMPOSE_PROFILES)
	docker compose up -d --build

down:            ## arrête TOUS les conteneurs, quel que soit leur profil (les volumes restent)
	docker compose --profile '*' down

ps:              ## état et santé des conteneurs
	docker compose ps

migrate:         ## crée ou met à jour les schémas raw et obs
	$(UV) alembic -c ingestion/alembic.ini upgrade head

generate:        ## génère les données jusqu'à maintenant : make generate SCALE=5
	$(LOAD_ENV) && ./srv-source/generator/generate.sh $(SCALE)

generate-break:  ## idem, plus des lignes qui violent le contrat (scénario 2)
	$(LOAD_ENV) && ./srv-source/generator/generate.sh $(SCALE) break

ingest:          ## ingestion incrémentale
	$(UV) python -m ingestion.load_raw

ingest-full:     ## relit toute la source : prouve l'idempotence (insérées = 0)
	$(UV) python -m ingestion.load_raw --full

reset:           ## scénario 5 : remet l'entrepôt à blanc
	$(UV) python -m ingestion.reset

test:            ## tests unitaires (rapides, sans base)
	$(UV) pytest -q

test-integration: ## tests qui demandent les conteneurs démarrés
	$(UV) pytest -q -m integration

lint:            ## style et erreurs probables
	uv run ruff check . && uv run ruff format --check .

check: lint test ## à lancer avant chaque commit

dbt-debug:       ## vérifie le profil dbt et la connexion à PostgreSQL 16
	$(UV) dbt debug --project-dir dbt --profiles-dir dbt

dbt-build:       ## construit et teste tous les modèles dbt
	$(UV) dbt build --project-dir dbt --profiles-dir dbt

raw-check: ; $(LOAD_ENV) && docker compose exec -T pg16 psql -U "$$WAREHOUSE_USER" -d "$$WAREHOUSE_DB" -X -q -v ON_ERROR_STOP=1 < scripts/check_ingested_at.sql

# ---------- Traçabilité : Marquez dans le pg16 existant (profil lineage) ----------
marquez-db:      ## crée ou met à jour la base et le rôle marquez dans pg16 (idempotent)
	@$(LOAD_ENV) && \
	  if [ -z "$${MARQUEZ_DB_PASSWORD:-}" ]; then echo "MARQUEZ_DB_PASSWORD manque dans .env" >&2; exit 1; fi && \
	  docker compose exec -T -e MARQUEZ_DB_PASSWORD pg16 \
	    psql -U "$$WAREHOUSE_USER" -d "$$WAREHOUSE_DB" -X -q < scripts/marquez_db.sql

lineage-up: marquez-db ## démarre Marquez (API et interface) après avoir préparé sa base
	docker compose --profile core --profile lineage up -d marquez marquez-web

lineage-check:   ## l'API Marquez répond-elle ? (liste les namespaces)
	@$(LOAD_ENV) && curl -fsS "http://localhost:$${MARQUEZ_API_PORT:-5000}/api/v1/namespaces" && echo

lineage-build:   ## dbt build + OpenLineage : Marquez si OPENLINEAGE_URL, sinon lineage/events.ndjson
	./scripts/lineage_build.sh $(ARGS)

obs-roles:       ## rôles monitor (pg16, pg13), grafana_ro et streamlit_app (pg16) ; idempotent
# Les scripts utilisent \getenv (psql 15+). Le psql du conteneur pg13 est en 13 : pour pg13,
# on prend donc le client psql 16 du conteneur pg16, qui joint pg13 par le réseau Docker.
	@$(LOAD_ENV) && \
	  MONITOR_PASSWORD="$$PG16_MONITOR_PASSWORD" docker compose exec -T -e MONITOR_PASSWORD pg16 \
	    psql -U "$$WAREHOUSE_USER" -d "$$WAREHOUSE_DB" -X -q < scripts/monitor_role.sql && \
	  MONITOR_PASSWORD="$$PG13_MONITOR_PASSWORD" PGPASSWORD="$$SOURCE_PASSWORD" \
	  docker compose exec -T -e MONITOR_PASSWORD -e PGPASSWORD pg16 \
	    psql -h pg13 -U "$$SOURCE_USER" -d "$$SOURCE_DB" -X -q < scripts/monitor_role.sql && \
	  docker compose exec -T -e PG16_GRAFANA_PASSWORD -e PG16_STREAMLIT_PASSWORD pg16 \
	    psql -U "$$WAREHOUSE_USER" -d "$$WAREHOUSE_DB" -X -q < scripts/obs_roles.sql

# ---------- Surveillance et pilotage : toujours avec le profil core, dont ils dépendent ----------
obs-up: obs-roles ## démarre Prometheus, Grafana (:3001), les exporters, cAdvisor et Mailpit (:8025)
	docker compose --profile core --profile observability up -d

pilotage-up: obs-roles ## construit et démarre le runner et Streamlit dans Docker : http://localhost:8501
	docker compose --profile core --profile pilotage up -d --build runner streamlit
