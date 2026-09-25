"""drop this service's table copies from workspace schemas

The OSM Rails website creates each workspace schema with Apartment, which
(`use_sql = true`) runs `pg_dump -n public` and loads the result into the new
schema. Every workspace created after this service's tables were added to
`public` therefore carries its own empty copies of them.

Those copies were harmless until a `SET search_path TO 'workspace-N', public`
leaked onto pooled connections (fixed by scoping it with SET LOCAL). With the
path leaked, unqualified names resolved to the copies: creates inserted into
`public.jobs` and then could not read the row back, and some writes landed in
a workspace schema's copy instead of `public`, where nothing reads them.

For each workspace schema this merges any rows in the copied role and tasking
tables into `public`, with new ids (each copy numbered its own rows from 1,
so every id collides), and then drops the copies. `jobs` rows are not
merged: they are imports that never ran and would start days late.

A copy holding rows this migration does not know how to merge is left in
place and reported, rather than dropped, so nothing is lost. This runs at
application start, so it also avoids raising.

Revision ID: b7d4e2a9c1f0
Revises: 37c12e8301ee
Create Date: 2026-09-25 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "b7d4e2a9c1f0"
down_revision: Union[str, None] = "37c12e8301ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Merged in this order, so each table's references are already remapped.
# (table, has its own serial id, {column: table whose id map it follows})
MERGED_TABLES: list[tuple[str, bool, dict[str, str]]] = [
    ("user_workspace_roles", False, {}),
    ("tasking_projects", True, {}),
    ("tasking_tasks", True, {"project_id": "tasking_projects"}),
    ("tasking_project_roles", False, {"project_id": "tasking_projects"}),
    (
        "tasking_locks",
        True,
        {"project_id": "tasking_projects", "task_id": "tasking_tasks"},
    ),
    (
        "tasking_changesets",
        True,
        {
            "project_id": "tasking_projects",
            "task_id": "tasking_tasks",
            "lock_id": "tasking_locks",
        },
    ),
    (
        "tasking_feedback",
        True,
        {"project_id": "tasking_projects", "task_id": "tasking_tasks"},
    ),
    (
        "tasking_audit_events",
        True,
        {"project_id": "tasking_projects", "task_id": "tasking_tasks"},
    ),
    (
        "tasking_task_save_idempotency",
        False,
        {"project_id": "tasking_projects"},
    ),
]

# Copied tables whose rows are deliberately discarded.
DISCARDED_TABLES = ["jobs", "alembic_version"]

# Every table this service owns in `public`, and so may find copied.
OWNED_TABLES = [t for t, _, _ in MERGED_TABLES] + ["teams", "team_user"]

OWNED_ENUMS = [
    "workspace_role",
    "tasking_project_status",
    "tasking_task_boundary_type",
    "tasking_task_status",
    "tasking_lock_release_reason",
    "tasking_feedback_reason",
]


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables_in(conn: Connection, schema: str) -> set[str]:
    return set(
        conn.execute(
            text(
                "SELECT c.relname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relkind = 'r'"
            ),
            {"s": schema},
        ).scalars()
    )


def _columns(conn: Connection, schema: str, table: str) -> dict[str, tuple]:
    """Column name -> (udt schema, udt name), in table order."""
    rows = conn.execute(
        text(
            "SELECT column_name, udt_schema, udt_name"
            " FROM information_schema.columns"
            " WHERE table_schema = :s AND table_name = :t"
            " ORDER BY ordinal_position"
        ),
        {"s": schema, "t": table},
    )
    return {r.column_name: (r.udt_schema, r.udt_name) for r in rows}


def _merge_table(
    conn: Connection,
    schema: str,
    table: str,
    has_id: bool,
    remaps: dict[str, str],
) -> int:
    src = f"{_q(schema)}.{_q(table)}"
    public_cols = _columns(conn, "public", table)
    copy_cols = _columns(conn, schema, table)
    cols = [c for c in public_cols if c in copy_cols]

    if has_id:
        conn.execute(
            text(
                f"INSERT INTO _copy_id_map (tbl, old_id, new_id)"
                f" SELECT :t, id,"
                f" nextval(pg_get_serial_sequence('public.{_q(table)}', 'id'))"
                f" FROM {src}"
            ),
            {"t": table},
        )

    exprs = []
    for col in cols:
        if col == "id" and has_id:
            expr = (
                f"(SELECT new_id FROM _copy_id_map"
                f" WHERE tbl = '{table}' AND old_id = x.id)"
            )
        elif col in remaps:
            expr = (
                f"COALESCE((SELECT new_id FROM _copy_id_map"
                f" WHERE tbl = '{remaps[col]}' AND old_id = x.{_q(col)}),"
                f" x.{_q(col)})"
            )
        elif col == "name" and table == "tasking_projects":
            # Project names are unique per workspace among live projects; the
            # owner may since have recreated one that went missing.
            expr = (
                "CASE WHEN EXISTS (SELECT 1 FROM public.tasking_projects p"
                " WHERE p.workspace_id = x.workspace_id"
                " AND lower(p.name) = lower(x.name) AND p.deleted_at IS NULL)"
                " THEN x.name || ' (recovered)' ELSE x.name END"
            )
        else:
            expr = f"x.{_q(col)}"

        # The copy's enum columns use the copy's own enum types, which do not
        # cast implicitly to public's.
        udt_schema, udt_name = public_cols[col]
        if copy_cols[col][0] == schema and udt_schema == "public":
            expr = f"({expr})::text::public.{_q(udt_name)}"

        exprs.append(expr)

    col_list = ", ".join(_q(c) for c in cols)
    result = conn.execute(
        text(
            f"INSERT INTO public.{_q(table)} ({col_list}) OVERRIDING SYSTEM VALUE"
            f" SELECT {', '.join(exprs)} FROM {src} x"
            f" ON CONFLICT DO NOTHING"
        )
    )
    return result.rowcount


def merge_and_drop_copies(conn: Connection) -> None:
    schemas = conn.execute(
        text(
            "SELECT nspname FROM pg_namespace"
            " WHERE nspname ~ '^workspace-[0-9]+$' ORDER BY nspname"
        )
    ).scalars()

    conn.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS _copy_id_map"
            " (tbl text, old_id bigint, new_id bigint) ON COMMIT DROP"
        )
    )

    for schema in list(schemas):
        present = _tables_in(conn, schema) & set(OWNED_TABLES + DISCARDED_TABLES)
        if not present:
            continue

        conn.execute(text("TRUNCATE _copy_id_map"))
        kept = set()

        for table, has_id, remaps in MERGED_TABLES:
            if table in present:
                merged = _merge_table(conn, schema, table, has_id, remaps)
                if merged:
                    print(f"{schema}: merged {merged} row(s) of {table} into public")

        # Owned tables with no merge rule: keep any that hold rows.
        for table in present - {t for t, _, _ in MERGED_TABLES} - set(DISCARDED_TABLES):
            count = conn.execute(
                text(f"SELECT count(*) FROM {_q(schema)}.{_q(table)}")
            ).scalar_one()
            if count:
                kept.add(table)
                print(f"{schema}: kept {table}, which holds {count} unmerged row(s)")

        for table in sorted(present - kept):
            conn.execute(text(f"DROP TABLE {_q(schema)}.{_q(table)} CASCADE"))

        if not kept:
            for enum in OWNED_ENUMS:
                conn.execute(text(f"DROP TYPE IF EXISTS {_q(schema)}.{_q(enum)}"))


def upgrade() -> None:
    merge_and_drop_copies(op.get_bind())


def downgrade() -> None:
    # The copies were never meant to exist, and merged rows cannot be told
    # apart from ones written to public directly.
    pass
