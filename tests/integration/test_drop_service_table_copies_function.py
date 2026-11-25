"""Integration tests for public.drop_service_table_copies (a7f3c9e2d1b8).

The backend and the importer both call it after creating a workspace, so its
snapshot of this service's tables must keep up with SERVICE_TABLES and
SERVICE_ENUMS.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from api.src.osm.repository import SERVICE_ENUMS, SERVICE_TABLES

pytestmark = pytest.mark.integration

WS = "workspace-930001"


async def test_drop_function_covers_every_service_table(_migrated_db):
    """A workspace with a copy of every table and enum in SERVICE_TABLES and
    SERVICE_ENUMS is left with none of them. If this fails after adding to
    either list, add a revision that replaces the function."""
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{WS}"'))
            for table in SERVICE_TABLES:
                await conn.execute(
                    text(
                        f'CREATE TABLE "{WS}".{table}'
                        f" (LIKE public.{table} INCLUDING DEFAULTS)"
                    )
                )
            for enum in SERVICE_ENUMS:
                await conn.execute(text(f"CREATE TYPE \"{WS}\".{enum} AS ENUM ('x')"))
            # A table Rails put there, which must survive.
            await conn.execute(text(f'CREATE TABLE "{WS}".nodes (id bigint)'))

        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT public.drop_service_table_copies(:s)"), {"s": WS}
            )

        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT relname FROM pg_class"
                            " WHERE relnamespace = to_regnamespace(:s)"
                            " AND relkind = 'r'"
                        ),
                        {"s": f'"{WS}"'},
                    )
                ).scalars()
            )
            enums = set(
                (
                    await conn.execute(
                        text(
                            "SELECT typname FROM pg_type"
                            " WHERE typnamespace = to_regnamespace(:s)"
                            " AND typtype = 'e'"
                        ),
                        {"s": f'"{WS}"'},
                    )
                ).scalars()
            )
            assert (tables, enums) == ({"nodes"}, set())
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{WS}" CASCADE'))
        await engine.dispose()


@pytest.mark.parametrize("schema", ["public", "workspace-1; DROP TABLE x", ""])
async def test_drop_function_refuses_anything_but_a_workspace_schema(
    _migrated_db, schema
):
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            with pytest.raises(DBAPIError, match="not a workspace schema"):
                await conn.execute(
                    text("SELECT public.drop_service_table_copies(:s)"),
                    {"s": schema},
                )
        async with engine.connect() as conn:
            # public's own tables are untouched.
            jobs = (
                await conn.execute(text("SELECT to_regclass('public.jobs')"))
            ).scalar_one()
            assert jobs is not None
    finally:
        await engine.dispose()


async def test_drop_function_tolerates_a_missing_schema(_migrated_db):
    """The importer calls it right after the create; if that create failed,
    there is no schema, and the call must not fail too."""
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT public.drop_service_table_copies('workspace-939999')")
            )
    finally:
        await engine.dispose()
