"""Integration tests for the 6dafe2da5f69 migration.

It mirrors nine upstream OSM Rails migrations into `public` with the DDL Rails
generates, records their versions in `schema_migrations`, and adds the two
notification tables to every workspace schema, one schema per transaction.

The expected definitions below are upstream openstreetmap-website's
db/structure.sql, so these tests fail if the mirror drifts from Rails.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

pytestmark = pytest.mark.integration

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic_osm/versions/6dafe2da5f69_mirror_rails_upstream_migrations.py"
)
_spec = importlib.util.spec_from_file_location("mirror_rails", _MIGRATION)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

NOTIFICATION_TABLES = ["noticed_events", "noticed_notifications"]

# Rails objects the integration stub lacks but these migrations touch, shaped
# as in Rails' structure.sql before the nine migrations.
_RAILS_PREREQUISITES = [
    "CREATE TYPE public.format_enum AS ENUM ('html', 'markdown', 'text')",
    "CREATE TYPE public.gpx_visibility_enum"
    " AS ENUM ('private', 'public', 'trackable', 'identifiable')",
    "CREATE TABLE public.gpx_files (id bigserial PRIMARY KEY, user_id bigint NOT NULL,"
    " visibility public.gpx_visibility_enum"
    " DEFAULT 'public'::public.gpx_visibility_enum NOT NULL)",
    "CREATE INDEX gpx_files_user_id_idx ON public.gpx_files USING btree (user_id)",
    "CREATE TABLE public.schema_migrations"
    " (version character varying NOT NULL PRIMARY KEY)",
]
_RAILS_CLEANUP = [
    "DROP TABLE IF EXISTS public.moderation_zones",
    "DROP TABLE IF EXISTS public.gpx_files",
    "DROP TABLE IF EXISTS public.schema_migrations",
    "DROP TYPE IF EXISTS public.format_enum",
    "DROP TYPE IF EXISTS public.gpx_visibility_enum",
]

_TS = "timestamp(6) without time zone"

# (name, type, NOT NULL, default) per column, from upstream db/structure.sql.
EXPECTED_COLUMNS = {
    "spammy_phrases": [
        ("id", "bigint", True, "nextval('spammy_phrases_id_seq'::regclass)"),
        ("phrase", "character varying", False, None),
        ("created_at", _TS, True, None),
        ("updated_at", _TS, True, None),
    ],
    "moderation_zones": [
        ("id", "bigint", True, "nextval('moderation_zones_id_seq'::regclass)"),
        ("name", "character varying", True, None),
        ("reason", "character varying", True, None),
        ("reason_format", "format_enum", False, "'markdown'::format_enum"),
        ("zone", "geometry(Polygon,4326)", True, None),
        ("ends_at", _TS, True, None),
        ("creator_id", "bigint", True, None),
        ("revoker_id", "bigint", False, None),
        ("created_at", _TS, True, None),
        ("updated_at", _TS, True, None),
    ],
    "noticed_events": [
        ("id", "bigint", True, "nextval('noticed_events_id_seq'::regclass)"),
        ("type", "character varying", False, None),
        ("record_type", "character varying", False, None),
        ("record_id", "bigint", False, None),
        ("notifications_count", "integer", False, None),
        ("params", "jsonb", False, None),
        ("created_at", _TS, True, None),
        ("updated_at", _TS, True, None),
    ],
    "noticed_notifications": [
        ("id", "bigint", True, "nextval('noticed_notifications_id_seq'::regclass)"),
        ("type", "character varying", False, None),
        ("event_id", "bigint", True, None),
        ("recipient_type", "character varying", True, None),
        ("recipient_id", "bigint", True, None),
        ("read_at", _TS, False, None),
        ("seen_at", _TS, False, None),
        ("created_at", _TS, True, None),
        ("updated_at", _TS, True, None),
    ],
}

EXPECTED_INDEXES = {
    "spammy_phrases": {
        "CREATE UNIQUE INDEX spammy_phrases_pkey"
        " ON public.spammy_phrases USING btree (id)",
    },
    "moderation_zones": {
        "CREATE UNIQUE INDEX moderation_zones_pkey"
        " ON public.moderation_zones USING btree (id)",
        "CREATE INDEX index_moderation_zones_on_creator_id"
        " ON public.moderation_zones USING btree (creator_id)",
        "CREATE INDEX index_moderation_zones_on_revoker_id"
        " ON public.moderation_zones USING btree (revoker_id)",
    },
    "noticed_events": {
        "CREATE UNIQUE INDEX noticed_events_pkey"
        " ON public.noticed_events USING btree (id)",
        "CREATE INDEX index_noticed_events_on_record"
        " ON public.noticed_events USING btree (record_type, record_id)",
    },
    "noticed_notifications": {
        "CREATE UNIQUE INDEX noticed_notifications_pkey"
        " ON public.noticed_notifications USING btree (id)",
        "CREATE INDEX index_noticed_notifications_on_event_id"
        " ON public.noticed_notifications USING btree (event_id)",
        "CREATE INDEX index_noticed_notifications_on_recipient"
        " ON public.noticed_notifications USING btree (recipient_type, recipient_id)",
    },
}

EXPECTED_MODERATION_ZONES_CONSTRAINTS = {
    "moderation_zones_pkey": "PRIMARY KEY (id)",
    "fk_rails_6a0b70e3da": "FOREIGN KEY (creator_id) REFERENCES users(id)",
    "fk_rails_f2132b7340": "FOREIGN KEY (revoker_id) REFERENCES users(id)",
}


# (name, type, NOT NULL, default expression)
Column = tuple[str, str, bool, str | None]


async def _columns(conn: AsyncConnection, schema: str, table: str) -> list[Column]:
    rows = await conn.execute(
        text(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,"
            " pg_get_expr(d.adbin, d.adrelid)"
            " FROM pg_attribute a"
            " LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
            " WHERE a.attrelid = to_regclass(:t) AND a.attnum > 0"
            " AND NOT a.attisdropped ORDER BY a.attnum"
        ),
        {"t": f'"{schema}"."{table}"'},
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]


async def _indexes(conn: AsyncConnection, schema: str, table: str) -> set[str]:
    rows = await conn.execute(
        text(
            "SELECT indexdef FROM pg_indexes"
            " WHERE schemaname = :s AND tablename = :t"
        ),
        {"s": schema, "t": table},
    )
    return set(rows.scalars())


async def _constraints(
    conn: AsyncConnection, schema: str, table: str
) -> dict[str, str]:
    rows = await conn.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = to_regclass(:t)"
        ),
        {"t": f'"{schema}"."{table}"'},
    )
    return {r[0]: r[1] for r in rows}


async def _versions(conn: AsyncConnection, schema: str) -> set[str]:
    rows = await conn.execute(text(f'SELECT version FROM "{schema}".schema_migrations'))
    return set(rows.scalars())


def _as_public(schema: str, value: str | None) -> str | None:
    """A workspace object's definition, written as if it were in public."""
    return None if value is None else value.replace(f'"{schema}".', "public.")


async def _matches_public(conn: AsyncConnection, schema: str, table: str) -> bool:
    """The workspace table is defined exactly as public's (columns, defaults,
    indexes), apart from naming its own schema."""
    public_columns = await _columns(conn, "public", table)
    columns = [
        (name, typ, notnull, _as_public(schema, default))
        for name, typ, notnull, default in await _columns(conn, schema, table)
    ]
    # public's own defaults are unqualified: public is on the search_path.
    public_columns = [
        (
            name,
            typ,
            notnull,
            None if d is None else d.replace("nextval('", "nextval('public."),
        )
        for name, typ, notnull, d in public_columns
    ]
    indexes = {_as_public(schema, i) for i in await _indexes(conn, schema, table)}
    return columns == public_columns and indexes == await _indexes(
        conn, "public", table
    )


async def test_public_matches_upstream_structure_sql(_migrated_db):
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)

    try:
        async with engine.begin() as conn:
            for statement in _RAILS_PREREQUISITES:
                await conn.execute(text(statement))

        async with engine.begin() as conn:
            applied = await conn.run_sync(migration.apply_public)
        assert applied == sorted(migration.VERSIONS)

        async with engine.connect() as conn:
            for table, expected in EXPECTED_COLUMNS.items():
                assert await _columns(conn, "public", table) == expected, table
                assert await _indexes(conn, "public", table) == EXPECTED_INDEXES[table]
            assert (
                await _constraints(conn, "public", "moderation_zones")
                == EXPECTED_MODERATION_ZONES_CONSTRAINTS
            )

            assert ("public_heatmap", "boolean", True, "true") in await _columns(
                conn, "public", "users"
            )
            assert await _indexes(conn, "public", "gpx_files") == {
                "CREATE UNIQUE INDEX gpx_files_pkey ON public.gpx_files USING btree (id)"
            }
            visibility = [
                c
                for c in await _columns(conn, "public", "gpx_files")
                if c[0] == "visibility"
            ]
            assert visibility == [
                (
                    "visibility",
                    "gpx_visibility_enum",
                    True,
                    "'trackable'::gpx_visibility_enum",
                )
            ]
            assert await _versions(conn, "public") == set(migration.VERSIONS)

        # Running it again changes nothing and does not fail.
        async with engine.begin() as conn:
            assert await conn.run_sync(migration.apply_public) == sorted(
                migration.VERSIONS
            )
        async with engine.connect() as conn:
            assert await _versions(conn, "public") == set(migration.VERSIONS)
    finally:
        async with engine.begin() as conn:
            for statement in _RAILS_CLEANUP:
                await conn.execute(text(statement))
        await engine.dispose()


async def test_finishes_moderation_zones_after_a_partial_rails_run(_migrated_db):
    """Rails created moderation_zones (nullable ends_at) and maybe step one's
    check constraint, but not step two: finish it as step two does."""
    _task_url, osm_url = _migrated_db
    engine = create_async_engine(osm_url)
    created_by_rails = migration.MODERATION_ZONES_COLUMNS.replace(
        f"ends_at {_TS} NOT NULL", f"ends_at {_TS}"
    )

    try:
        async with engine.begin() as conn:
            for statement in _RAILS_PREREQUISITES:
                await conn.execute(text(statement))
            await conn.execute(
                text(f"CREATE TABLE public.moderation_zones ({created_by_rails})")
            )
            # A zone with no end, from before step one...
            await conn.execute(
                text(
                    "INSERT INTO public.moderation_zones"
                    " (id, name, reason, zone, creator_id, created_at, updated_at)"
                    " VALUES (1, 'z', 'r',"
                    " 'SRID=4326;POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 1, now(), now())"
                )
            )
            # ...then step one's constraint, NOT VALID so existing rows pass.
            await conn.execute(
                text(
                    "ALTER TABLE public.moderation_zones"
                    " ADD CONSTRAINT moderation_zones_ends_at_null"
                    " CHECK (ends_at IS NOT NULL) NOT VALID"
                )
            )

        async with engine.begin() as conn:
            await conn.run_sync(migration.apply_public)

        async with engine.connect() as conn:
            ends_at = [
                c
                for c in await _columns(conn, "public", "moderation_zones")
                if c[0] == "ends_at"
            ]
            assert ends_at == [("ends_at", _TS, True, None)]
            assert "moderation_zones_ends_at_null" not in await _constraints(
                conn, "public", "moderation_zones"
            )
            unended = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM public.moderation_zones WHERE ends_at IS NULL"
                    )
                )
            ).scalar_one()
            assert unended == 0
    finally:
        async with engine.begin() as conn:
            for statement in _RAILS_CLEANUP:
                await conn.execute(text(statement))
        await engine.dispose()


# Prod has ~1,900 workspace schemas, and this migration does work in every one
# of them. 300 here exercises the one-transaction-per-schema loop at a scale
# where doing it in one transaction would exhaust CI's lock table.
MANY_SCHEMAS = 300
FIRST_MANY = 930000


async def test_every_workspace_schema_gets_the_notification_tables(
    _migrated_db, capsys
):
    _task_url, osm_url = _migrated_db
    schemas = [f"workspace-{FIRST_MANY + i}" for i in range(MANY_SCHEMAS)]
    no_versions = schemas[0]  # a schema without a schema_migrations copy
    already = schemas[1]  # cloned after the migration: has the tables already
    not_a_workspace = "workspace-notes"
    engine = create_async_engine(osm_url)

    try:
        for start in range(0, MANY_SCHEMAS, 50):
            async with engine.begin() as conn:
                for schema in schemas[start : start + 50]:
                    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                    if schema != no_versions:
                        await conn.execute(
                            text(
                                f'CREATE TABLE "{schema}".schema_migrations'
                                " (version character varying NOT NULL PRIMARY KEY)"
                            )
                        )
                        await conn.execute(
                            text(
                                f'INSERT INTO "{schema}".schema_migrations'
                                " VALUES ('20250101000000')"
                            )
                        )
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{not_a_workspace}"'))
            await conn.execute(text(migration._workspace_block(already)))

        async with engine.connect() as conn:
            await conn.run_sync(migration.add_notification_tables_to_workspaces)
        assert "0 left as they were after an error" in capsys.readouterr().out

        async with engine.connect() as conn:
            created = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = ANY(:s) AND c.relkind = 'r'"
                        " AND c.relname = ANY(:t)"
                    ),
                    {"s": schemas, "t": NOTIFICATION_TABLES},
                )
            ).scalar_one()
            assert created == 2 * MANY_SCHEMAS

            for schema in (no_versions, already, schemas[150], schemas[-1]):
                for table in NOTIFICATION_TABLES:
                    assert await _matches_public(conn, schema, table), (schema, table)
            for schema in schemas[1:]:
                versions = await _versions(conn, schema)
                assert versions == set(migration.VERSIONS) | {"20250101000000"}

            elsewhere = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_class c"
                        " JOIN pg_namespace n ON n.oid = c.relnamespace"
                        " WHERE n.nspname = :s"
                    ),
                    {"s": not_a_workspace},
                )
            ).scalar_one()
            assert elsewhere == 0

        # A second run finds nothing to do.
        async with engine.connect() as conn:
            await conn.run_sync(migration.add_notification_tables_to_workspaces)
        assert "every workspace schema has them" in capsys.readouterr().out
    finally:
        for schema in schemas + [not_a_workspace]:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


async def test_a_schema_that_fails_is_left_as_it_was_and_the_rest_still_run(
    _migrated_db, capsys
):
    _task_url, osm_url = _migrated_db
    bad, good = "workspace-940001", "workspace-940002"
    engine = create_async_engine(osm_url)

    try:
        async with engine.begin() as conn:
            for schema in (bad, good):
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            # Takes the name the id sequence needs, so the bad schema's block
            # fails partway, after creating noticed_events.
            await conn.execute(
                text(f'CREATE TABLE "{bad}".noticed_events_id_seq (x int)')
            )

        async with engine.connect() as conn:
            await conn.run_sync(migration.add_notification_tables_to_workspaces)
        out = capsys.readouterr().out
        assert f"{bad}: notification tables not added, left as it was" in out
        assert "1 left as they were after an error" in out

        async with engine.connect() as conn:
            for table in NOTIFICATION_TABLES:
                assert await _columns(conn, bad, table) == []
                assert await _matches_public(conn, good, table)
    finally:
        async with engine.begin() as conn:
            for schema in (bad, good):
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
