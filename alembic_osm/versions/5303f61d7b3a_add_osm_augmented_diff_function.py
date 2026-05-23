"""add osm_augmented_diff function

Revision ID: 5303f61d7b3a
Revises: a1b2c3d4e5f6
Create Date: 2026-06-17 00:00:00.000000

"""

import pathlib
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "5303f61d7b3a"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQL_DIR = pathlib.Path(__file__).parent.parent / "sql"


def upgrade() -> None:
    op.execute(text((_SQL_DIR / "osm_augmented_diff.sql").read_text()))


def downgrade() -> None:
    op.execute(text("DROP FUNCTION IF EXISTS osm_augmented_diff(bigint)"))
