"""add user role table

Revision ID: 9221408912dd
Revises:
Create Date: 2026-01-29 14:54:10.669000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9221408912dd"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False so the type is NOT implicitly created/dropped by the table
# DDL. We manage its lifecycle explicitly with checkfirst=True so the migration
# is idempotent whether or not the type already exists.
workspace_role = postgresql.ENUM(
    "lead", "validator", "contributor", name="workspace_role", create_type=False
)


def upgrade() -> None:
    workspace_role.create(op.get_bind(), checkfirst=True)
    op.create_unique_constraint("auth_uid_unique", "users", ["auth_uid"])
    op.create_table(
        "user_workspace_roles",
        sa.Column("user_auth_uid", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("role", workspace_role, nullable=False),
        sa.ForeignKeyConstraint(["user_auth_uid"], ["users.auth_uid"]),
        sa.PrimaryKeyConstraint("user_auth_uid", "workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("user_workspace_roles")
    workspace_role.drop(op.get_bind(), checkfirst=True)
    op.drop_constraint("auth_uid_unique", "users", type_="unique")
