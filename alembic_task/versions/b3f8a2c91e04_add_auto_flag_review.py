"""Add autoFlagReview column to workspaces table

Revision ID: b3f8a2c91e04
Revises: add6266277c7
Create Date: 2026-03-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "b3f8a2c91e04"
down_revision: Union[str, None] = "add6266277c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(name: str) -> bool:
    """True if `workspaces.<name>` already exists.

    Guards the add/drop so the migration is safe whether or not the column
    was created out-of-band (e.g. by a parallel branch) — a plain
    ``ADD COLUMN`` would raise ``DuplicateColumnError`` otherwise.
    """
    insp = inspect(op.get_bind())
    return name in {c["name"] for c in insp.get_columns("workspaces")}


def upgrade() -> None:
    if _has_column("autoFlagReview"):
        return
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "autoFlagReview",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            )
        )


def downgrade() -> None:
    if not _has_column("autoFlagReview"):
        return
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_column("autoFlagReview")
