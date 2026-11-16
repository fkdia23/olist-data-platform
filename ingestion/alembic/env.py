# from logging.config import fileConfig

# from sqlalchemy import engine_from_config
# from sqlalchemy import pool

# from alembic import context

# # this is the Alembic Config object, which provides
# # access to the values within the .ini file in use.
# config = context.config

# # Interpret the config file for Python logging.
# # This line sets up loggers basically.
# if config.config_file_name is not None:
#     fileConfig(config.config_file_name)

# # add your model's MetaData object here
# # for 'autogenerate' support
# # from myapp import mymodel
# # target_metadata = mymodel.Base.metadata
# target_metadata = None

# # other values from the config, defined by the needs of env.py,
# # can be acquired:
# # my_important_option = config.get_main_option("my_important_option")
# # ... etc.


# def run_migrations_offline() -> None:
#     """Run migrations in 'offline' mode.

#     This configures the context with just a URL
#     and not an Engine, though an Engine is acceptable
#     here as well.  By skipping the Engine creation
#     we don't even need a DBAPI to be available.

#     Calls to context.execute() here emit the given string to the
#     script output.

#     """
#     url = config.get_main_option("sqlalchemy.url")
#     context.configure(
#         url=url,
#         target_metadata=target_metadata,
#         literal_binds=True,
#         dialect_opts={"paramstyle": "named"},
#     )

#     with context.begin_transaction():
#         context.run_migrations()


# def run_migrations_online() -> None:
#     """Run migrations in 'online' mode.

#     In this scenario we need to create an Engine
#     and associate a connection with the context.

#     """
#     connectable = engine_from_config(
#         config.get_section(config.config_ini_section, {}),
#         prefix="sqlalchemy.",
#         poolclass=pool.NullPool,
#     )

#     with connectable.connect() as connection:
#         context.configure(
#             connection=connection, target_metadata=target_metadata
#         )

#         with context.begin_transaction():
#             context.run_migrations()


# if context.is_offline_mode():
#     run_migrations_offline()
# else:
#     run_migrations_online()


# ingestion/alembic/env.py
"""Point d'entrée d'Alembic : comment se connecter et exécuter les migrations."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config  # contenu de alembic.ini

if config.config_file_name is not None:
    fileConfig(config.config_file_name)  # configuration des logs définie dans alembic.ini

# Les migrations sont écrites à la main en SQL : pas de modèles SQLAlchemy, donc pas d'autogenerate.
target_metadata = None

# L'adresse vient de l'environnement (.env), jamais d'un fichier versionné.
# WAREHOUSE_DSN est au format libpq (postgresql://...) ; SQLAlchemy veut le nom du pilote
# dans le préfixe : on le transforme en postgresql+psycopg://...
DSN = os.environ.get("WAREHOUSE_DSN")
if not DSN:
    raise RuntimeError("WAREHOUSE_DSN n'est pas défini : charge ton .env avant de lancer Alembic")
DB_URL = DSN.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    """Mode hors ligne (alembic upgrade head --sql) : affiche le SQL sans l'exécuter."""
    context.configure(url=DB_URL, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Mode normal : se connecte et applique les migrations dans une transaction."""
    engine = create_engine(DB_URL, poolclass=pool.NullPool)  # NullPool : une connexion, puis fermée
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
