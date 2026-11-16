# runner/Dockerfile : contexte = racine du dépôt (le runner lance l'ingestion, dbt et le générateur)
FROM python:3.12-slim

# uv, copié depuis son image officielle (même version que srv-source/api)
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

# psql : srv-source/generator/generate.sh l'appelle pour remplir la source
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# 1. Dépendances : couche en cache tant que pyproject.toml et uv.lock ne changent pas
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group ingestion --group transformation --group runner --no-install-project
# Extension postgres de DuckDB téléchargée au build : mart.publish_duckdb n'a pas besoin d'internet
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL postgres')"

# 2. Paquets dbt (dbt_utils, dbt_expectations) : versions figées par package-lock.yml
COPY dbt/dbt_project.yml dbt/packages.yml dbt/package-lock.yml dbt/
RUN dbt deps --project-dir dbt

# 3. Le code, qui change souvent (.dockerignore écarte .venv, .env et les données)
COPY . .

EXPOSE 8000
# Un seul processus : le worker et le planificateur des crons vivent dans uvicorn.
# Deux runners feraient tourner deux planificateurs, donc chaque cron en double.
CMD ["uvicorn", "runner.app:app", "--host", "0.0.0.0", "--port", "8000"]
