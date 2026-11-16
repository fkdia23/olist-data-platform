# srv-source/api/main.py
"""API du fournisseur simulé : expose les tables de PostgreSQL 13 page par page.

Lancement local : uv run uvicorn main:app --app-dir srv-source/api --port 8000
"""

import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from prometheus_fastapi_instrumentator import Instrumentator
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Liste blanche : seules ces tables sont exposées (protège contre l'injection SQL)
TABLES = {
    "geolocation",
    "customers",
    "sellers",
    "products",
    "orders",
    "order_items",
    "order_payments",
    "order_reviews",
}
MAX_LIMIT = 10_000  # taille de page maximale autorisée

# Taux de pannes simulées (0 = aucune, 0.05 = 5 % de réponses 500), modifiable à chaud
state = {"chaos_rate": float(os.environ.get("CHAOS_RATE", "0"))}

# Pool de connexions : ouvert au démarrage, partagé entre les requêtes.
# row_factory=dict_row : chaque ligne devient un dict {colonne: valeur}, prêt pour le JSON.
pool = ConnectionPool(
    os.environ["SOURCE_DSN"], min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row}
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open(wait=True, timeout=30)  # échoue vite si la base est injoignable
    yield
    pool.close()


app = FastAPI(title="API source Olist", lifespan=lifespan)
Instrumentator().instrument(app).expose(app)  # ajoute /metrics (latence, codes HTTP)


@app.get("/health")
def health() -> dict:
    """Vérifie que l'API répond ET que la base est joignable."""
    with pool.connection() as conn:
        conn.execute("select 1")
    return {"status": "ok"}


@app.post("/admin/chaos")
def set_chaos(rate: float = Query(ge=0, le=1)) -> dict:
    """Règle le taux de pannes simulées, par exemple 0.05."""
    state["chaos_rate"] = rate
    return state


@app.get("/tables/{table}")
def read_table(
    table: str,
    after_id: int = Query(0, ge=0, description="renvoie les lignes dont _src_id > after_id"),
    limit: int = Query(5000, ge=1, le=MAX_LIMIT),
) -> dict:
    """Renvoie une page de lignes, triées par _src_id croissant (pagination par curseur)."""
    if table not in TABLES:
        raise HTTPException(status_code=404, detail=f"table inconnue : {table}")
    if random.random() < state["chaos_rate"]:
        raise HTTPException(status_code=500, detail="panne simulée")

    # sql.Identifier échappe le nom de table ; les valeurs passent en paramètres (%s)
    query = sql.SQL("select * from {} where _src_id > %s order by _src_id limit %s").format(
        sql.Identifier(table)
    )
    with pool.connection() as conn:
        rows = conn.execute(query, (after_id, limit)).fetchall()

    next_after_id = rows[-1]["_src_id"] if rows else after_id
    return {"table": table, "count": len(rows), "next_after_id": next_after_id, "rows": rows}
