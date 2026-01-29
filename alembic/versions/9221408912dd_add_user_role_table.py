"""add user role table

Revision ID: 9221408912dd
Revises: add6266277c7
Create Date: 2026-01-29 14:54:10.669000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9221408912dd'
down_revision: Union[str, None] = 'add6266277c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add unique constraint on users.auth_uid
    op.create_unique_constraint('auth_uid_unique', 'users', ['auth_uid'])

    # Create the workspace_role enum type
    workspace_role = sa.Enum('lead', 'validator', 'contributor', name='workspace_role')
    workspace_role.create(op.get_bind())

    # Create the user_workspace_roles table
    op.create_table(
        'user_workspace_roles',
        sa.Column('user_auth_uid', sa.String(), nullable=False),
        sa.Column('workspace_id', sa.BigInteger(), nullable=False),
        sa.Column('role', sa.Enum('lead', 'validator', 'contributor', name='workspace_role', create_type=False), nullable=False),
        sa.ForeignKeyConstraint(['user_auth_uid'], ['users.auth_uid']),
        sa.PrimaryKeyConstraint('user_auth_uid', 'workspace_id')
    )


def downgrade() -> None:
    op.drop_table('user_workspace_roles')

    # Drop the enum type
    workspace_role = sa.Enum('lead', 'validator', 'contributor', name='workspace_role')
    workspace_role.drop(op.get_bind())

    op.drop_constraint('auth_uid_unique', 'users', type_='unique')
