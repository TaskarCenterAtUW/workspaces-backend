"""mirror OSM Rails' upstream migrations of 2025-11 to 2026-06

The OSM schema belongs to osm-rails (workspaces-openstreetmap-website), which
is behind upstream openstreetmap-website. This revision applies the nine
upstream migrations added since that fork's last upstream sync (2025-09-01),
so the schema matches upstream Rails whether or not `rails db:migrate` has run:

    20251121134648  create spammy_phrases
    20251218105716  add users.public_heatmap
    20260113142804  enable the postgis extension
    20260113144310  create moderation_zones
    20260218183352  drop gpx_files_user_id_idx
    20260223105922  create noticed_events and noticed_notifications
    20260604103822  moderation_zones: ends_at check constraint (step one)
    20260604105008  moderation_zones: ends_at NOT NULL (step two)
    20260624101500  gpx_files.visibility defaults to 'trackable'

The DDL is what Rails generates for these migrations (upstream
db/structure.sql), statement for statement. Each version is recorded in
`schema_migrations`, so a later `rails db:migrate` treats them as done instead
of failing on objects that already exist.

Workspace schemas also get the two notification tables. Rails records a
notification whenever someone comments on a changeset or note through the API,
and it does so inside the workspace (Apartment): the Noticed models are not in
Apartment's excluded_models, so the tables must exist in each workspace schema.
New workspaces get them when Apartment clones `public`; this adds them to the
existing ones and records the versions there too, so Rails' per-tenant
migrate does not try to apply them (`users` has no copy in a workspace schema,
so `add_column :users` would fail there). The other changes stay in `public`:
nothing that runs inside a workspace reads them.

It also records the rails fork's own CreateTeam (20251028201000) as applied,
without running it. This service owns `teams` and `team_user` (a1b2c3d4e5f6
creates them; they are in SERVICE_TABLES), Rails never reads or writes them,
and its definition differs (bigint ids, nullable columns, a unique
(workspace_id, name) index). Left pending, `rails db:migrate` would fail on
the existing tables and stop before every later migration.

Every step checks before it changes anything, so this is safe on a database
where Rails has already applied some or all of these, and on the integration
tests' stub, which has no gpx_files, format_enum or schema_migrations.

Revision ID: 6dafe2da5f69
Revises: b7d4e2a9c1f0
Create Date: 2026-11-24 00:00:00.000000

"""

import time
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "6dafe2da5f69"
down_revision: Union[str, None] = "b7d4e2a9c1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The Rails versions this revision mirrors, oldest first.
VERSIONS = [
    "20251121134648",
    "20251218105716",
    "20260113142804",
    "20260113144310",
    "20260218183352",
    "20260223105922",
    "20260604103822",
    "20260604105008",
    "20260624101500",
]

# The rails fork's CreateTeam: recorded as applied, never run (see above).
CREATE_TEAM_VERSION = "20251028201000"

# Every version this revision records.
ALL_VERSIONS = sorted(VERSIONS + [CREATE_TEAM_VERSION])

# Column lists are copied verbatim from upstream db/structure.sql.
SPAMMY_PHRASES_COLUMNS = """
    id bigint NOT NULL,
    phrase character varying,
    created_at timestamp(6) without time zone NOT NULL,
    updated_at timestamp(6) without time zone NOT NULL
"""

MODERATION_ZONES_COLUMNS = """
    id bigint NOT NULL,
    name character varying NOT NULL,
    reason character varying NOT NULL,
    reason_format public.format_enum DEFAULT 'markdown'::public.format_enum,
    zone public.geometry(Polygon,4326) NOT NULL,
    ends_at timestamp(6) without time zone NOT NULL,
    creator_id bigint NOT NULL,
    revoker_id bigint,
    created_at timestamp(6) without time zone NOT NULL,
    updated_at timestamp(6) without time zone NOT NULL
"""
MODERATION_ZONES_INDEXES = [
    ("index_moderation_zones_on_creator_id", "creator_id"),
    ("index_moderation_zones_on_revoker_id", "revoker_id"),
]
MODERATION_ZONES_FOREIGN_KEYS = [
    "ALTER TABLE ONLY public.moderation_zones ADD CONSTRAINT fk_rails_6a0b70e3da"
    " FOREIGN KEY (creator_id) REFERENCES public.users(id)",
    "ALTER TABLE ONLY public.moderation_zones ADD CONSTRAINT fk_rails_f2132b7340"
    " FOREIGN KEY (revoker_id) REFERENCES public.users(id)",
]

NOTIFICATION_TABLES = {
    "noticed_events": (
        """
    id bigint NOT NULL,
    type character varying,
    record_type character varying,
    record_id bigint,
    notifications_count integer,
    params jsonb,
    created_at timestamp(6) without time zone NOT NULL,
    updated_at timestamp(6) without time zone NOT NULL
""",
        [("index_noticed_events_on_record", "record_type, record_id")],
    ),
    "noticed_notifications": (
        """
    id bigint NOT NULL,
    type character varying,
    event_id bigint NOT NULL,
    recipient_type character varying NOT NULL,
    recipient_id bigint NOT NULL,
    read_at timestamp(6) without time zone,
    seen_at timestamp(6) without time zone,
    created_at timestamp(6) without time zone NOT NULL,
    updated_at timestamp(6) without time zone NOT NULL
""",
        [
            ("index_noticed_notifications_on_event_id", "event_id"),
            (
                "index_noticed_notifications_on_recipient",
                "recipient_type, recipient_id",
            ),
        ],
    ),
}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _create_table_sql(
    schema: str,
    table: str,
    columns: str,
    indexes: Sequence[tuple[str, str]] = (),
) -> list[str]:
    """The statements structure.sql uses for a Rails table with a bigint id:
    the table, its id sequence, the sequence's owner and default, the primary
    key, then the indexes."""
    s, t = _q(schema), _q(table)
    seq = f"{s}.{_q(table + '_id_seq')}"
    return [
        f"CREATE TABLE {s}.{t} ({columns})",
        f"CREATE SEQUENCE {seq} START WITH 1 INCREMENT BY 1"
        " NO MINVALUE NO MAXVALUE CACHE 1",
        f"ALTER SEQUENCE {seq} OWNED BY {s}.{t}.id",
        f"ALTER TABLE ONLY {s}.{t} ALTER COLUMN id"
        f" SET DEFAULT nextval('{seq}'::regclass)",
        f"ALTER TABLE ONLY {s}.{t}"
        f" ADD CONSTRAINT {_q(table + '_pkey')} PRIMARY KEY (id)",
    ] + [
        f"CREATE INDEX {_q(name)} ON {s}.{t} USING btree ({cols})"
        for name, cols in indexes
    ]


def _run(conn: Connection, statements: list[str]) -> None:
    for statement in statements:
        conn.execute(text(statement))


def _has_table(conn: Connection, schema: str, table: str) -> bool:
    return conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"),
        {"n": f"{_q(schema)}.{_q(table)}"},
    ).scalar_one()


def _has_extension(conn: Connection, name: str) -> bool:
    return conn.execute(
        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = :n)"),
        {"n": name},
    ).scalar_one()


def _has_type(conn: Connection, schema: str, name: str) -> bool:
    return conn.execute(
        text("SELECT to_regtype(:n) IS NOT NULL"),
        {"n": f"{_q(schema)}.{_q(name)}"},
    ).scalar_one()


def _is_nullable(conn: Connection, schema: str, table: str, column: str) -> bool:
    return conn.execute(
        text(
            "SELECT is_nullable = 'YES' FROM information_schema.columns"
            " WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": schema, "t": table, "c": column},
    ).scalar_one()


def _record_versions_sql(schema: str, versions: Sequence[str]) -> str:
    values = ", ".join(f"('{v}')" for v in versions)
    return (
        f"INSERT INTO {_q(schema)}.schema_migrations (version)"
        f" VALUES {values} ON CONFLICT DO NOTHING"
    )


def apply_public(conn: Connection) -> list[str]:
    """Apply the nine migrations to `public`. Returns the versions now in
    effect there (ALL_VERSIONS, except on a database missing a Rails table or
    type a migration needs, such as the integration tests' stub)."""
    applied: list[str] = []

    # 20260113142804 enable_postgis. First: moderation_zones needs geometry.
    # Only when missing (a1b2c3d4e5f6 already requires it, so it never is in
    # a deployed database): Azure Database for PostgreSQL refuses CREATE
    # EXTENSION postgis, even with IF NOT EXISTS, to roles outside
    # azure_pg_admin, and stage's app role is one.
    if not _has_extension(conn, "postgis"):
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public"))
    applied.append("20260113142804")

    # 20251121134648 create_spammy_phrases
    if not _has_table(conn, "public", "spammy_phrases"):
        _run(
            conn,
            _create_table_sql("public", "spammy_phrases", SPAMMY_PHRASES_COLUMNS),
        )
    applied.append("20251121134648")

    # 20251218105716 add_public_heatmap_to_users
    if _has_table(conn, "public", "users"):
        conn.execute(
            text(
                "ALTER TABLE public.users"
                " ADD COLUMN IF NOT EXISTS public_heatmap boolean DEFAULT true NOT NULL"
            )
        )
        applied.append("20251218105716")
    else:
        print("public: no users table; skipped users.public_heatmap")

    # 20260113144310 create_moderation_zones, then 20260604103822 and
    # 20260604105008, which leave ends_at NOT NULL and no check constraint.
    moderation = ["20260113144310", "20260604103822", "20260604105008"]
    if _has_table(conn, "public", "moderation_zones"):
        # Rails created it. Finish the two follow-up migrations if they have
        # not run; step two ends every existing zone, as Rails' does.
        if _is_nullable(conn, "public", "moderation_zones", "ends_at"):
            conn.execute(text("UPDATE public.moderation_zones SET ends_at = NOW()"))
            conn.execute(
                text(
                    "ALTER TABLE public.moderation_zones"
                    " ALTER COLUMN ends_at SET NOT NULL"
                )
            )
        conn.execute(
            text(
                "ALTER TABLE public.moderation_zones"
                " DROP CONSTRAINT IF EXISTS moderation_zones_ends_at_null"
            )
        )
        applied += moderation
    elif _has_type(conn, "public", "format_enum") and _has_table(
        conn, "public", "users"
    ):
        _run(
            conn,
            _create_table_sql(
                "public",
                "moderation_zones",
                MODERATION_ZONES_COLUMNS,
                MODERATION_ZONES_INDEXES,
            )
            + MODERATION_ZONES_FOREIGN_KEYS,
        )
        applied += moderation
    else:
        print("public: no format_enum type or users table; skipped moderation_zones")

    # 20260218183352 drop_gpx_files_user_id_index and
    # 20260624101500 change_gpx_files_visibility_default
    if _has_table(conn, "public", "gpx_files") and _has_type(
        conn, "public", "gpx_visibility_enum"
    ):
        conn.execute(text("DROP INDEX IF EXISTS public.gpx_files_user_id_idx"))
        conn.execute(
            text(
                "ALTER TABLE public.gpx_files ALTER COLUMN visibility"
                " SET DEFAULT 'trackable'::public.gpx_visibility_enum"
            )
        )
        applied += ["20260218183352", "20260624101500"]
    else:
        print("public: no gpx_files table; skipped its index and default changes")

    # 20260223105922 create_noticed_tables
    for table, (columns, indexes) in NOTIFICATION_TABLES.items():
        if not _has_table(conn, "public", table):
            _run(conn, _create_table_sql("public", table, columns, indexes))
    applied.append("20260223105922")

    # The fork's CreateTeam: recorded, not run, once this service's own
    # `teams` and `team_user` exist (see CREATE_TEAM_VERSION).
    if _has_table(conn, "public", "teams") and _has_table(conn, "public", "team_user"):
        applied.append(CREATE_TEAM_VERSION)

    if _has_table(conn, "public", "schema_migrations"):
        conn.execute(text(_record_versions_sql("public", sorted(applied))))
    return sorted(applied)


def _workspace_block(schema: str) -> str:
    """One round trip for one workspace schema: create whichever notification
    table is missing, and record ALL_VERSIONS if the schema has a
    schema_migrations copy."""
    s = _q(schema)
    parts: list[str] = []
    for table, (columns, indexes) in NOTIFICATION_TABLES.items():
        statements = ";\n    ".join(_create_table_sql(schema, table, columns, indexes))
        parts.append(
            f"IF to_regclass('{s}.{_q(table)}') IS NULL THEN\n"
            f"    {statements};\n"
            f"  END IF;"
        )
    parts.append(
        f"IF to_regclass('{s}.schema_migrations') IS NOT NULL THEN\n"
        f"    {_record_versions_sql(schema, ALL_VERSIONS)};\n"
        f"  END IF;"
    )
    return "DO $mirror$\nBEGIN\n  " + "\n  ".join(parts) + "\nEND\n$mirror$"


def add_notification_tables_to_workspaces(conn: Connection) -> None:
    """Add the notification tables to every workspace schema that lacks them,
    one schema per transaction.

    Per CLAUDE.md (and b7d4e2a9c1f0): a transaction per schema keeps the lock
    count small (one transaction across ~1,900 prod schemas would exhaust
    max_locks_per_transaction), makes each schema atomic, lets an interrupted
    run resume, and lets a failing schema be reported without stopping the
    rest or the app. Unlike b7d4e2a9c1f0, every workspace needs work here, so
    the schemas share one connection (Alembic's engine does not pool) and each
    is a single DO block, to keep ~1,900 schemas inside the startup time limit.
    """
    schemas = list(
        conn.execute(
            text(
                "SELECT n.nspname FROM pg_namespace n"
                " WHERE n.nspname ~ '^workspace-[0-9]+$'"
                " AND (to_regclass(quote_ident(n.nspname) || '.noticed_events')"
                " IS NULL OR to_regclass(quote_ident(n.nspname)"
                " || '.noticed_notifications') IS NULL)"
                " ORDER BY n.nspname"
            )
        ).scalars()
    )
    if not schemas:
        print("Workspace notification tables: every workspace schema has them")
        return

    start = time.monotonic()
    done = failed = 0
    ws_conn: Connection | None = None
    try:
        for i, schema in enumerate(schemas, 1):
            try:
                if ws_conn is None:
                    ws_conn = conn.engine.connect()
                with ws_conn.begin():
                    ws_conn.execute(text(_workspace_block(schema)))
                done += 1
            except Exception as e:  # noqa: BLE001 -- reported, and the rest continue
                failed += 1
                print(
                    f"{schema}: notification tables not added, left as it was:"
                    f" {type(e).__name__}: {e}"
                )
                if ws_conn is not None:
                    ws_conn.close()
                    ws_conn = None
            if i % 250 == 0:
                print(
                    f"Workspace notification tables: {i}/{len(schemas)} schema(s)"
                    f" in {time.monotonic() - start:.0f}s"
                )
    finally:
        if ws_conn is not None:
            ws_conn.close()

    print(
        f"Workspace notification tables: added to {done} schema(s), {failed} left"
        f" as they were after an error, in {time.monotonic() - start:.0f}s"
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Commit Alembic's transaction first. When several revisions run in one
    # upgrade (a new database, or one a few revisions behind), it still holds
    # the locks earlier revisions took -- including on `users`, which they FK
    # to -- and the separate connections below would wait on it forever.
    with op.get_context().autocommit_block():
        # `public` next, in its own transaction committed before the workspace
        # pass: it holds its locks on `users` and `gpx_files` only for these
        # few statements, not for the whole pass, and any workspace created
        # after it is cloned with the notification tables already in place.
        with bind.engine.begin() as public_conn:
            applied = apply_public(public_conn)
        print(f"public: Rails migrations in effect: {', '.join(applied)}")
        add_notification_tables_to_workspaces(bind)


def downgrade() -> None:
    # These are osm-rails' migrations; reverse them with `rails db:rollback`,
    # which also keeps `schema_migrations` in step. Dropping them from here
    # would lose notifications and leave Rails believing they still exist.
    pass
