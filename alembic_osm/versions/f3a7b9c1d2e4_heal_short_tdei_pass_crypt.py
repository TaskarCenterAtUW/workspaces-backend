"""heal short TDEI-provisioned users.pass_crypt

TDEI users are provisioned by the backend via raw SQL with a placeholder
``pass_crypt``. Older rows used a 4-char value (``"none"``), which violates OSM's
``pass_crypt`` length 8..255 ``User`` validation. That makes the user invalid,
and Rails operations that re-validate the author via ``validates :author,
:associated => true`` (posting a changeset comment or a note comment) then fail
to save. Replace any too-short value with a random throwaway -- TDEI manages
auth, so ``pass_crypt`` is never used to authenticate.

Revision ID: f3a7b9c1d2e4
Revises: 5303f61d7b3a
Create Date: 2026-07-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision: str = "f3a7b9c1d2e4"
down_revision: Union[str, None] = "5303f61d7b3a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # `users` is owned by the OSM Rails website, not this tree; guard so the
    # migration is a no-op if it hasn't been created yet.
    if not inspect(bind).has_table("users"):
        return

    # Idempotent: re-running only touches rows that are still too short.
    op.execute(
        text(
            "UPDATE users SET pass_crypt = md5(random()::text) "
            "WHERE auth_provider = 'TDEI' AND length(pass_crypt) < 8"
        )
    )


def downgrade() -> None:
    # The original short values are unrecoverable (and were invalid anyway).
    pass
