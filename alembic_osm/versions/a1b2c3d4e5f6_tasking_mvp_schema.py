from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9221408912dd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum names + values, declared once and reused on up/down.
TASKING_PROJECT_STATUS = ("draft", "open", "done")
TASKING_TASK_STATUS = ("to_map", "to_review", "to_remap", "completed")
TASKING_TASK_BOUNDARY_TYPE = ("grid", "import")
TASKING_LOCK_RELEASE_REASON = (
    "auto_unlock",
    "manual",
    "lead_release",
    "stale_timeout",
    "reset",
)
TASKING_FEEDBACK_REASON = (
    "incomplete_mapping",
    "data_quality_issue",
    "wrong_area",
    "other",
)


def _create_enum_if_absent(bind, name: str, values: tuple[str, ...]) -> None:
    exists = bind.execute(
        text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}
    ).scalar()
    if not exists:
        sa.Enum(*values, name=name).create(bind)


def _drop_enum_if_present(bind, name: str) -> None:
    exists = bind.execute(
        text("SELECT 1 FROM pg_type WHERE typname = :n"), {"n": name}
    ).scalar()
    if exists:
        bind.execute(text(f'DROP TYPE IF EXISTS "{name}"'))


def _postgis_available(bind) -> bool:
    return bool(
        bind.execute(
            text("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    use_postgis = _postgis_available(bind)
    if use_postgis:
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # ---- teams / team_user -------------------------------------------
    #
    # Created here so the OSM tree owns every table that references
    # `users.id`. The `has_table` guards keep this idempotent in both
    # production (shared TASK/OSM database) and fresh test installs.

    if not insp.has_table("teams"):
        op.create_table(
            "teams",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_teams_workspace_id", "teams", ["workspace_id"])

    if not insp.has_table("team_user"):
        op.create_table(
            "team_user",
            sa.Column("team_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("team_id", "user_id"),
        )

    # ---- Enums --------------------------------------------------------

    _create_enum_if_absent(bind, "tasking_project_status", TASKING_PROJECT_STATUS)
    _create_enum_if_absent(bind, "tasking_task_status", TASKING_TASK_STATUS)
    _create_enum_if_absent(
        bind, "tasking_task_boundary_type", TASKING_TASK_BOUNDARY_TYPE
    )
    _create_enum_if_absent(
        bind, "tasking_lock_release_reason", TASKING_LOCK_RELEASE_REASON
    )
    _create_enum_if_absent(bind, "tasking_feedback_reason", TASKING_FEEDBACK_REASON)

    # ---- tasking_projects --------------------------------------------

    if not insp.has_table("tasking_projects"):
        op.create_table(
            "tasking_projects",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            # Cross-DB ref to workspaces.id — no FK by design (matches
            # user_workspace_roles convention).
            sa.Column("workspace_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("instructions", sa.Text(), nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    *TASKING_PROJECT_STATUS,
                    name="tasking_project_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="draft",
            ),
            sa.Column(
                "review_required",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "lock_timeout_hours",
                sa.Integer(),
                nullable=False,
                server_default="8",
            ),
            sa.Column(
                "task_boundary_type",
                postgresql.ENUM(
                    *TASKING_TASK_BOUNDARY_TYPE,
                    name="tasking_task_boundary_type",
                    create_type=False,
                ),
                nullable=True,
            ),
            # AOI is MultiPolygon in EPSG:4326. Polygon inputs are
            # upcast to single-member MultiPolygons in the app layer.
            sa.Column(
                "aoi",
                sa.dialects.postgresql.BYTEA(),  # placeholder; replaced below
                nullable=True,
            ),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("created_by_name", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )

        if use_postgis:
            op.execute("ALTER TABLE tasking_projects DROP COLUMN aoi")
            op.execute(
                "ALTER TABLE tasking_projects "
                "ADD COLUMN aoi GEOMETRY(MultiPolygon, 4326)"
            )

        # Unique project name per workspace among non-deleted rows.
        op.execute(
            "CREATE UNIQUE INDEX tasking_projects_workspace_name_unique "
            "ON tasking_projects (workspace_id, lower(name)) "
            "WHERE deleted_at IS NULL"
        )

        op.create_index(
            "tasking_projects_workspace_idx",
            "tasking_projects",
            ["workspace_id"],
        )
        op.create_index(
            "tasking_projects_status_idx",
            "tasking_projects",
            ["status"],
        )

    # ---- tasking_project_roles ---------------------------------------

    if not insp.has_table("tasking_project_roles"):
        op.create_table(
            "tasking_project_roles",
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("user_auth_uid", sa.String(), nullable=False),
            sa.Column(
                "role",
                postgresql.ENUM(
                    "lead",
                    "validator",
                    "contributor",
                    name="workspace_role",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_auth_uid"], ["users.auth_uid"]),
            sa.PrimaryKeyConstraint("project_id", "user_auth_uid"),
        )

    # ---- tasking_tasks ------------------------------------------------

    if not insp.has_table("tasking_tasks"):
        op.create_table(
            "tasking_tasks",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("task_number", sa.Integer(), nullable=False),
            sa.Column("area_sqkm", sa.Numeric(precision=10, scale=4), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    *TASKING_TASK_STATUS,
                    name="tasking_task_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="to_map",
            ),
            sa.Column("last_mapper_id", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint(
                "project_id", "task_number", name="tasking_tasks_pn_unique"
            ),
        )
        if use_postgis:
            op.execute(
                "ALTER TABLE tasking_tasks "
                "ADD COLUMN geometry GEOMETRY(Polygon, 4326) NOT NULL"
            )
            op.execute(
                "CREATE INDEX tasking_tasks_geometry_idx "
                "ON tasking_tasks USING GIST (geometry)"
            )
        else:
            op.execute("ALTER TABLE tasking_tasks " "ADD COLUMN geometry BYTEA")
        op.create_index("tasking_tasks_project_idx", "tasking_tasks", ["project_id"])

    # ---- tasking_locks ------------------------------------------------

    if not insp.has_table("tasking_locks"):
        op.create_table(
            "tasking_locks",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("task_id", sa.BigInteger(), nullable=False),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("user_auth_uid", sa.String(), nullable=False),
            sa.Column(
                "task_status_at_lock",
                postgresql.ENUM(
                    *TASKING_TASK_STATUS,
                    name="tasking_task_status",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "locked_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "release_reason",
                postgresql.ENUM(
                    *TASKING_LOCK_RELEASE_REASON,
                    name="tasking_lock_release_reason",
                    create_type=False,
                ),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasking_tasks.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_auth_uid"], ["users.auth_uid"]),
        )
        # One active lock per task; one active lock per (project, user).
        op.execute(
            "CREATE UNIQUE INDEX tasking_locks_one_active_per_task "
            "ON tasking_locks (task_id) WHERE released_at IS NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX tasking_locks_one_active_per_user_project "
            "ON tasking_locks (project_id, user_auth_uid) "
            "WHERE released_at IS NULL"
        )
        op.execute(
            "CREATE INDEX tasking_locks_expiry_idx "
            "ON tasking_locks (expires_at) WHERE released_at IS NULL"
        )

    # ---- tasking_changesets ------------------------------------------

    if not insp.has_table("tasking_changesets"):
        op.create_table(
            "tasking_changesets",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("task_id", sa.BigInteger(), nullable=False),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("lock_id", sa.BigInteger(), nullable=False),
            sa.Column("user_auth_uid", sa.String(), nullable=False),
            sa.Column("osm_changeset_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "submitted_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasking_tasks.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["lock_id"], ["tasking_locks.id"]),
            sa.ForeignKeyConstraint(["user_auth_uid"], ["users.auth_uid"]),
        )
        op.create_index(
            "tasking_changesets_task_idx", "tasking_changesets", ["task_id"]
        )

    # ---- tasking_feedback --------------------------------------------
    #
    # Generic per-task feedback table. Covers validator remap rejections
    # (originally the only use case) plus any other free-form notes a
    # contributor / validator / lead may attach to a task — approval
    # comments, follow-up reminders, etc.
    #
    # `reason_category` is nullable: required only when the feedback is
    # used to drive a `to_review → to_remap` transition; left NULL for
    # generic notes. `notes` is required so a row always has at least
    # one of (category, free text) — usually both.

    if not insp.has_table("tasking_feedback"):
        op.create_table(
            "tasking_feedback",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("task_id", sa.BigInteger(), nullable=False),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("author_user_auth_uid", sa.String(), nullable=False),
            sa.Column(
                "reason_category",
                postgresql.ENUM(
                    *TASKING_FEEDBACK_REASON,
                    name="tasking_feedback_reason",
                    create_type=False,
                ),
                nullable=True,
            ),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["task_id"], ["tasking_tasks.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["author_user_auth_uid"], ["users.auth_uid"]),
        )
        op.create_index("tasking_feedback_task_idx", "tasking_feedback", ["task_id"])
        op.create_index(
            "tasking_feedback_project_idx", "tasking_feedback", ["project_id"]
        )

    # ---- tasking_audit_events ----------------------------------------

    if not insp.has_table("tasking_audit_events"):
        op.create_table(
            "tasking_audit_events",
            sa.Column(
                "id",
                sa.BigInteger(),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("task_id", sa.BigInteger(), nullable=True),
            sa.Column("actor_user_auth_uid", sa.Uuid(), nullable=False),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "details",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "project_deleted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            # No FK to tasking_projects so audit survives the
            # project's hard-delete of children + soft-delete itself.
        )
        op.create_index(
            "tasking_audit_project_idx", "tasking_audit_events", ["project_id"]
        )
        op.create_index(
            "tasking_audit_project_task_idx",
            "tasking_audit_events",
            ["project_id", "task_id"],
        )
        op.create_index(
            "tasking_audit_occurred_idx",
            "tasking_audit_events",
            ["occurred_at"],
        )

    # ---- tasking_task_save_idempotency -------------------------------

    if not insp.has_table("tasking_task_save_idempotency"):
        op.create_table(
            "tasking_task_save_idempotency",
            sa.Column("project_id", sa.BigInteger(), nullable=False),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("body_hash", sa.String(length=128), nullable=False),
            sa.Column(
                "response_json",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["tasking_projects.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("project_id", "key"),
        )
        op.create_index(
            "tasking_task_save_idempotency_created_idx",
            "tasking_task_save_idempotency",
            ["created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    # Drop tables in reverse FK order.
    for table in (
        "tasking_task_save_idempotency",
        "tasking_audit_events",
        "tasking_feedback",
        "tasking_changesets",
        "tasking_locks",
        "tasking_tasks",
        "tasking_project_roles",
        "tasking_projects",
        "team_user",
        "teams",
    ):
        if insp.has_table(table):
            op.drop_table(table)

    # Drop tasking-specific enums (workspace_role is owned by an earlier
    # revision and stays).
    for enum_name in (
        "tasking_feedback_reason",
        "tasking_lock_release_reason",
        "tasking_task_boundary_type",
        "tasking_task_status",
        "tasking_project_status",
    ):
        _drop_enum_if_present(bind, enum_name)
