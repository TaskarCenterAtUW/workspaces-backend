"""Integration test for the b7d4e2a9c1f0 migration.

Workspace schemas are cloned from `public`, so they can hold copies of this
service's tables, numbered from 1 and with their own enum types. The migration
merges any rows in those copies into `public` under new ids and drops them.
"""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic_osm/versions/b7d4e2a9c1f0_drop_workspace_table_copies.py"
)
_spec = importlib.util.spec_from_file_location("drop_copies", _MIGRATION)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

WS = "workspace-900001"
COPIED = ["jobs", "user_workspace_roles", "tasking_projects", "tasking_tasks"]


async def test_merges_copied_rows_under_new_ids_and_drops_copies(_migrated_db):
    _task_url, osm_url = _migrated_db
    user = str(uuid4())
    engine = create_async_engine(osm_url)

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (auth_uid, email, display_name)"
                    " VALUES (:u, :email, 'Copy Test')"
                ),
                {"u": user, "email": f"{user}@test.local"},
            )
            # Rows already in public, holding the ids the copy reuses.
            project_id = (
                await conn.execute(
                    text(
                        "INSERT INTO public.tasking_projects"
                        " (workspace_id, name, created_by)"
                        " VALUES (900001, 'Project 01', :u) RETURNING id"
                    ),
                    {"u": user},
                )
            ).scalar_one()
            task_id = (
                await conn.execute(
                    text(
                        "INSERT INTO public.tasking_tasks"
                        " (project_id, task_number, area_sqkm, geometry)"
                        " VALUES (:p, 1, 1.0, 'SRID=4326;POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))') RETURNING id"
                    ),
                    {"p": project_id},
                )
            ).scalar_one()

            # A workspace schema shaped like Apartment's clone.
            await conn.execute(text(f'CREATE SCHEMA "{WS}"'))
            for table in COPIED:
                await conn.execute(
                    text(
                        f'CREATE TABLE "{WS}".{table}'
                        f" (LIKE public.{table} INCLUDING DEFAULTS)"
                    )
                )
            await conn.execute(
                text(
                    f'CREATE TYPE "{WS}".workspace_role'
                    " AS ENUM ('lead', 'validator', 'contributor')"
                )
            )
            await conn.execute(
                text(
                    f'ALTER TABLE "{WS}".user_workspace_roles ALTER COLUMN role'
                    f' TYPE "{WS}".workspace_role USING role::text::"{WS}".workspace_role'
                )
            )

            # Rows written through a leaked search_path: same ids as public's,
            # and a project name the owner has since reused.
            await conn.execute(
                text(
                    f'INSERT INTO "{WS}".tasking_projects'
                    " (id, workspace_id, name, created_by)"
                    " VALUES (:p, 900001, 'Project 01', :u)"
                ),
                {"p": project_id, "u": user},
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{WS}".tasking_tasks'
                    " (id, project_id, task_number, area_sqkm, geometry)"
                    " VALUES (:t, :p, 1, 2.5, 'SRID=4326;POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))')"
                ),
                {"t": task_id, "p": project_id},
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{WS}".user_workspace_roles'
                    " (user_auth_uid, workspace_id, role)"
                    " VALUES (:u, 900001, 'lead')"
                ),
                {"u": user},
            )
            await conn.execute(
                text(
                    f'INSERT INTO "{WS}".jobs (id, job_type, status, request)'
                    " VALUES (1, 'copy-test-import', 'requested', '{}')"
                )
            )

            await conn.run_sync(migration.merge_and_drop_copies)

        async with engine.connect() as conn:
            recovered = (
                await conn.execute(
                    text(
                        "SELECT id FROM public.tasking_projects"
                        " WHERE workspace_id = 900001"
                        " AND name = 'Project 01 (recovered)'"
                    )
                )
            ).scalar_one()
            assert recovered != project_id

            # The task follows its project to the new id.
            area = (
                await conn.execute(
                    text(
                        "SELECT area_sqkm FROM public.tasking_tasks"
                        " WHERE project_id = :p"
                    ),
                    {"p": recovered},
                )
            ).scalar_one()
            assert area == pytest.approx(2.5)

            role = (
                await conn.execute(
                    text(
                        "SELECT role::text FROM public.user_workspace_roles"
                        " WHERE user_auth_uid = :u AND workspace_id = 900001"
                    ),
                    {"u": user},
                )
            ).scalar_one()
            assert role == "lead"

            # Stranded jobs are not merged.
            jobs = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM public.jobs"
                        " WHERE job_type = 'copy-test-import'"
                    )
                )
            ).scalar_one()
            assert jobs == 0

            left = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = :s"
                    ),
                    {"s": WS},
                )
            ).scalar_one()
            enum = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_type t"
                        " JOIN pg_namespace n ON n.oid = t.typnamespace"
                        " WHERE n.nspname = :s AND t.typname = 'workspace_role'"
                    ),
                    {"s": WS},
                )
            ).scalar_one()
            assert (left, enum) == (0, 0)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{WS}" CASCADE'))
        await engine.dispose()


async def test_drop_at_creation_removes_only_this_services_copies(_migrated_db):
    from sqlmodel.ext.asyncio.session import AsyncSession

    from api.src.osm.repository import OSMRepository

    _task_url, osm_url = _migrated_db
    schema = "workspace-900002"
    engine = create_async_engine(osm_url)

    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            for table in ["jobs", "tasking_projects", "user_workspace_roles"]:
                await conn.execute(
                    text(
                        f'CREATE TABLE "{schema}".{table}'
                        f" (LIKE public.{table} INCLUDING DEFAULTS)"
                    )
                )
            await conn.execute(
                text(
                    f'CREATE TYPE "{schema}".workspace_role'
                    " AS ENUM ('lead', 'validator', 'contributor')"
                )
            )
            # A table Rails put there, which must survive.
            await conn.execute(text(f'CREATE TABLE "{schema}".nodes (id bigint)'))

        async with AsyncSession(engine) as session:
            await OSMRepository(session).dropServiceTableCopies(900002)

        async with engine.connect() as conn:
            tables = set(
                (
                    await conn.execute(
                        text(
                            "SELECT c.relname FROM pg_class c"
                            " JOIN pg_namespace n ON n.oid = c.relnamespace"
                            " WHERE n.nspname = :s AND c.relkind = 'r'"
                        ),
                        {"s": schema},
                    )
                ).scalars()
            )
            enums = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_type t"
                        " JOIN pg_namespace n ON n.oid = t.typnamespace"
                        " WHERE n.nspname = :s AND t.typtype = 'e'"
                    ),
                    {"s": schema},
                )
            ).scalar_one()
            assert (tables, enums) == ({"nodes"}, 0)
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
