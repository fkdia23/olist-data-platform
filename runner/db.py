# runner/db.py
import os
from sqlalchemy import create_engine

# pool_pre_ping : vérifie la connexion avant usage, utile après un redémarrage de PostgreSQL
engine = create_engine(os.environ["PG16_RUNNER_URL"], pool_pre_ping=True)