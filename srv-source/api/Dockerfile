# srv-source/api/Dockerfile
FROM python:3.12-slim

# uv, copié depuis son image officielle (version épinglée)
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# 1. Les dépendances d'abord : couche en cache tant que pyproject.toml et uv.lock ne changent pas
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group api --no-install-project

# 2. Puis le code, qui change souvent
COPY srv-source/api/main.py ./main.py

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
