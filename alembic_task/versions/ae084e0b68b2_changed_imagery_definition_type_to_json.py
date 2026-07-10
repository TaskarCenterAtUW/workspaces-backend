"""Changed Imagery Definition type to JSON

Revision ID: ae084e0b68b2
Revises: 073a96d114ab
Create Date: 2025-09-09 10:23:31.169629

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ae084e0b68b2"
down_revision = "073a96d114ab"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE workspaces_imagery
        ALTER COLUMN definition
        TYPE JSON
        USING definition::json
    """)


def downgrade():
    op.execute("""
        ALTER TABLE workspaces_imagery
        ALTER COLUMN definition
        TYPE VARCHAR
        USING definition::text
    """)
