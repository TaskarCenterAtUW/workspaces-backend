import importlib
import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import geoalchemy2.alembic_helpers  # noqa: F401
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from api.core.config import settings
from api.core.database import Base

# Importing geoalchemy2.alembic_helpers (above) registers the geospatial
# operations (`op.create_geospatial_table` / `create_geospatial_index`) that the
# tasking-manager migration `cbc419d1740c` uses to create the `workspaces`
# table. A bare `from geoalchemy2 import Geometry` does NOT register them, so
# without this import that migration fails at upgrade time.


# Automatically import all models
src_path = Path(__file__).parent.parent / "api" / "src"
for path in src_path.rglob("*.py"):
    if path.name != "__init__.py":
        module_path = str(path.relative_to(Path(__file__).parent.parent)).replace(
            os.sep, "."
        )[:-3]
        try:
            importlib.import_module(module_path)
        except Exception as e:
            print(f"Failed to import {module_path}: {e}")

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Run migrations with a SYNCHRONOUS psycopg2 driver, even though the app uses
# asyncpg at runtime. Many of the imported tasking-manager migrations were
# authored for psycopg2 and use patterns asyncpg rejects — most notably
# multi-statement `op.execute` (asyncpg runs every statement as a prepared
# statement and raises "cannot insert multiple commands into a prepared
# statement", e.g. the full-text-search trigger in 451f6bd05a19). Only the
# driver in the URL is swapped; host/database/credentials are untouched.
config.set_main_option(
    "sqlalchemy.url", settings.TASK_DATABASE_URL.replace("+asyncpg", "+psycopg2")
)

# Add your model's MetaData object here for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a synchronous engine."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
