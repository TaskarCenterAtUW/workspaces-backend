"""add user role table

Revision ID: 9221408912dd
Revises:
Create Date: 2026-01-29 14:54:10.669000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "9221408912dd"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    # `users` is normally owned and migrated by the OSM Rails app. When
    # running against a fresh database without Rails (CI/testcontainers,
    # or a dev box without the osm-rails service), create a minimal stub
    # so the FK from `tasking_project_roles`/`user_workspace_roles` can
    # be added below. Guarded by has_table so the production-owned
    # schema wins when it is already present.
    if not insp.has_table("users"):
        op.create_table(
            "users",
            # `id` matches the Rails `users.id` numeric PK so the FK
            # from `team_user.user_id` in the next migration can attach.
            sa.Column(
                "id", sa.BigInteger(), autoincrement=True, nullable=False
            ),
            sa.Column("auth_uid", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("auth_uid", name="auth_uid_unique"),
        )

    # Add unique constraint on users.auth_uid (if not already present)
    constraint_exists = bind.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = 'auth_uid_unique'")
    ).scalar()
    if not constraint_exists:
        op.create_unique_constraint("auth_uid_unique", "users", ["auth_uid"])

    # Create the workspace_role enum type (if not already present)
    result = bind.execute(
        text("SELECT 1 FROM pg_type WHERE typname = 'workspace_role'")
    )
    if not result.scalar():
        workspace_role = sa.Enum(
            "lead", "validator", "contributor", name="workspace_role"
        )
        workspace_role.create(bind)

    # Create the user_workspace_roles table (if not already present)
    if not insp.has_table("user_workspace_roles"):
        op.create_table(
            "user_workspace_roles",
            sa.Column("user_auth_uid", sa.String(), nullable=False),
            sa.Column("workspace_id", sa.BigInteger(), nullable=False),
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
            sa.ForeignKeyConstraint(["user_auth_uid"], ["users.auth_uid"]),
            sa.PrimaryKeyConstraint("user_auth_uid", "workspace_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    if insp.has_table("user_workspace_roles"):
        op.drop_table("user_workspace_roles")

    # Drop the enum type
    result = bind.execute(
        text("SELECT 1 FROM pg_type WHERE typname = 'workspace_role'")
    )
    if result.scalar():
        workspace_role = sa.Enum(
            "lead", "validator", "contributor", name="workspace_role"
        )
        workspace_role.drop(bind)

    constraint_exists = bind.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = 'auth_uid_unique'")
    ).scalar()
    if constraint_exists:
        op.drop_constraint("auth_uid_unique", "users", type_="unique")
