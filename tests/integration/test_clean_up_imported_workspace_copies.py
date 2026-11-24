"""Integration test for the e5c2a8d4f7b1 migration.

It re-runs b7d4e2a9c1f0's cleanup of this service's table copies, and deletes
OSM logins the token bridge wrote into workspace schemas' copies of the OAuth
tables, leaving `public` and the copied tables themselves in place.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.integration

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic_osm/versions/e5c2a8d4f7b1_clean_up_imported_workspace_copies.py"
)
_spec = importlib.util.spec_from_file_location("clean_up_imported", _MIGRATION)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

WITH_LOGINS = "workspace-920001"
EMPTY = "workspace-920002"

# Just the shape that matters here: tokens and grants reference applications.
_OAUTH_DDL = [
    "CREATE TABLE {s}.oauth_applications (id bigserial PRIMARY KEY, uid varchar NOT NULL)",
    "CREATE TABLE {s}.oauth_access_tokens (id bigserial PRIMARY KEY,"
    " application_id bigint REFERENCES {s}.oauth_applications (id), token varchar)",
    "CREATE TABLE {s}.oauth_access_grants (id bigserial PRIMARY KEY,"
    " application_id bigint REFERENCES {s}.oauth_applications (id))",
]


async def _count(conn: AsyncConnection, schema: str, table: str) -> int:
    return (
        await conn.execute(text(f'SELECT count(*) FROM "{schema}".{table}'))
    ).scalar_one()


async def _tables(conn: AsyncConnection, schema: str) -> set[str]:
    return set(
        (
            await conn.execute(
                text(
                    "SELECT relname FROM pg_class"
                    " WHERE relnamespace = to_regnamespace(:s) AND relkind = 'r'"
                ),
                {"s": f'"{schema}"'},
            )
        ).scalars()
    )


async def test_drops_service_copies_and_deletes_misplaced_logins(_migrated_db):
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)

    try:
        async with engine.begin() as conn:
            for schema in ("public", f'"{WITH_LOGINS}"', f'"{EMPTY}"'):
                if schema != "public":
                    await conn.execute(text(f"CREATE SCHEMA {schema}"))
                for ddl in _OAUTH_DDL:
                    await conn.execute(text(ddl.format(s=schema)))
            # The real login records, in public: these must stay.
            await conn.execute(
                text(
                    "INSERT INTO public.oauth_applications (id, uid) VALUES (1, 'app')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO public.oauth_access_tokens (application_id, token)"
                    " VALUES (1, 'kept')"
                )
            )
            # The bridge's misplaced copies, in a workspace schema.
            await conn.execute(
                text(
                    f'INSERT INTO "{WITH_LOGINS}".oauth_applications (id, uid)'
                    " VALUES (1, 'app')"
                )
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{WITH_LOGINS}".oauth_access_tokens'
                    " (application_id, token) VALUES (1, 'a'), (1, 'b')"
                )
            )
            # An imported workspace's empty copies of this service's tables.
            for schema in (WITH_LOGINS, EMPTY):
                for table in ("jobs", "tasking_projects"):
                    await conn.execute(
                        text(
                            f'CREATE TABLE "{schema}".{table}'
                            f" (LIKE public.{table} INCLUDING DEFAULTS)"
                        )
                    )

        async with engine.connect() as conn:
            await conn.run_sync(migration._copies_cleanup().merge_and_drop_copies)
            await conn.run_sync(migration.delete_misplaced_oauth_rows)

        async with engine.connect() as conn:
            for schema in (WITH_LOGINS, EMPTY):
                tables = await _tables(conn, schema)
                assert not tables & {"jobs", "tasking_projects"}, schema
                # The OAuth tables stay, empty.
                assert set(migration.OAUTH_TABLES) <= tables, schema
                for table in migration.OAUTH_TABLES:
                    assert await _count(conn, schema, table) == 0, (schema, table)
            assert await _count(conn, "public", "oauth_applications") == 1
            assert await _count(conn, "public", "oauth_access_tokens") == 1
    finally:
        async with engine.begin() as conn:
            for schema in (WITH_LOGINS, EMPTY):
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            for table in (
                "oauth_access_grants",
                "oauth_access_tokens",
                "oauth_applications",
            ):
                await conn.execute(text(f"DROP TABLE IF EXISTS public.{table}"))
        await engine.dispose()


async def test_nothing_to_do_is_quiet_and_harmless(_migrated_db):
    """No workspace schema holds copies or logins: both passes find nothing."""
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)
    try:
        async with engine.connect() as conn:
            assert await conn.run_sync(migration._schemas_with_oauth_rows) == []
            await conn.run_sync(migration.delete_misplaced_oauth_rows)
    finally:
        await engine.dispose()
