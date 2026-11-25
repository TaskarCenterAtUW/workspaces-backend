"""add public.drop_service_table_copies(schema), shared with the importer

Rails creates each workspace schema by cloning `public`, so a new workspace
starts with copies of this service's tables and enum types. The backend drops
them right after the create it proxies (`OSMRepository.dropServiceTableCopies`),
but the importer can create workspaces by calling the OSM gateway directly,
and those kept their copies (see e5c2a8d4f7b1).

This puts the drop in the database, so the backend and the importer run the
same code with the same list, and neither repository carries a copy of it.
The list is a snapshot of SERVICE_TABLES and SERVICE_ENUMS
(`api/src/osm/repository.py`). When either changes, add a revision that
replaces this function; `test_drop_function_covers_every_service_table` fails
until one does.

Apartment's clone copies this function into each new workspace schema too,
as it already does Rails' own (`api_rate_limit`, ...). Callers always name
`public.drop_service_table_copies`, so the copies are never used.

Revision ID: a7f3c9e2d1b8
Revises: e5c2a8d4f7b1
Create Date: 2026-11-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f3c9e2d1b8"
down_revision: Union[str, None] = "e5c2a8d4f7b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# SERVICE_TABLES and SERVICE_ENUMS as of this revision.
TABLES = [
    "alembic_version",
    "jobs",
    "teams",
    "team_user",
    "user_workspace_roles",
    "tasking_projects",
    "tasking_tasks",
    "tasking_project_roles",
    "tasking_locks",
    "tasking_changesets",
    "tasking_feedback",
    "tasking_audit_events",
    "tasking_task_save_idempotency",
]
ENUMS = [
    "workspace_role",
    "tasking_project_status",
    "tasking_task_boundary_type",
    "tasking_task_status",
    "tasking_lock_release_reason",
    "tasking_feedback_reason",
]


def _array(names: list[str]) -> str:
    return "ARRAY[" + ", ".join(f"'{n}'" for n in names) + "]::text[]"


def upgrade() -> None:
    op.execute(f"""
        CREATE OR REPLACE FUNCTION public.drop_service_table_copies(ws_schema text)
        RETURNS void
        LANGUAGE plpgsql
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
            name text;
        BEGIN
            -- Only ever a workspace schema: never public, whose tables these are.
            IF ws_schema !~ '^workspace-[0-9]+$' THEN
                RAISE EXCEPTION 'not a workspace schema: %', ws_schema;
            END IF;
            FOREACH name IN ARRAY {_array(TABLES)} LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I.%I CASCADE', ws_schema, name);
            END LOOP;
            FOREACH name IN ARRAY {_array(ENUMS)} LOOP
                EXECUTE format('DROP TYPE IF EXISTS %I.%I', ws_schema, name);
            END LOOP;
        END
        $fn$
        """)
    op.execute(
        "COMMENT ON FUNCTION public.drop_service_table_copies(text) IS"
        " 'Drops workspaces-backend''s table and enum copies from a new workspace"
        " schema. Owned by workspaces-backend migrations; called by the backend"
        " and workspaces-importer after creating a workspace.'"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.drop_service_table_copies(text)")
