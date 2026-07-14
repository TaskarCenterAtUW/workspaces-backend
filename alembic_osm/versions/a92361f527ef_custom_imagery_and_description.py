"""custom_imagery_and_description

Revision ID: a92361f527ef
Revises: f3a7b9c1d2e4
Create Date: 2026-07-14 15:08:42.142553

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a92361f527ef"
down_revision: Union[str, None] = "f3a7b9c1d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasking_projects",
        sa.Column("custom_imagery", JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "tasking_projects",
        sa.Column("description", sa.String(length=10000), nullable=True),
    )
    pass


def downgrade() -> None:
    op.drop_column("tasking_projects", "custom_imagery")
    op.drop_column("tasking_projects", "description")
    pass
