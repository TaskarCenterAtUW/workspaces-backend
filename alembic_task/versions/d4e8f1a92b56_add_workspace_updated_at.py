"""Add updatedAt column to workspaces table

Revision ID: d4e8f1a92b56
Revises: b3f8a2c91e04
Create Date: 2026-08-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8f1a92b56"
down_revision: Union[str, None] = "b3f8a2c91e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, no backfill: rows written before this column existed read
    # back as None, and WorkspaceResponse.from_workspace (api/src/workspaces/
    # schemas.py) falls back to createdAt for those. New/updated rows get a
    # real value from the model's default=/onupdate=datetime.now.
    op.add_column("workspaces", sa.Column("updatedAt", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "updatedAt")
