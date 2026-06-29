from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

revision: str = "c5121cbba124"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _assert_postgis_installed(bind) -> None:
    """Require the postgis extension to be installed in this database."""
    installed = bool(
        bind.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
        ).scalar()
    )
    if not installed:
        raise RuntimeError(
            "postgis extension is not installed in this database. "
            "Run `CREATE EXTENSION IF NOT EXISTS postgis;` before migrations."
        )


def upgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    _assert_postgis_installed(bind)

    geometry_column = sa.Column(
        "geometry",
        Geometry(geometry_type="MULTIPOLYGON", srid=4326),
        nullable=True,
    )

    # The TASK tree owns `workspaces` and `workspaces_*` only.
    # `users`, `teams`, `team_user`, `user_workspace_roles`, and the
    # `tasking_*` tables are owned by the OSM tree.

    if not insp.has_table("workspaces"):
        op.create_table(
            "workspaces",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("tdeiProjectGroupId", sa.Uuid(), nullable=False),
            sa.Column("tdeiRecordId", sa.Uuid(), nullable=True),
            sa.Column("tdeiServiceId", sa.Uuid(), nullable=True),
            sa.Column(
                "tdeiMetadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            sa.Column("createdAt", sa.DateTime(), nullable=False),
            sa.Column("createdBy", sa.Uuid(), nullable=False),
            sa.Column("createdByName", sa.String(), nullable=False),
            geometry_column,
            sa.Column(
                "externalAppAccess",
                sa.SmallInteger(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("kartaViewToken", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if not insp.has_table("workspaces_long_quests"):
        op.create_table(
            "workspaces_long_quests",
            sa.Column("workspace_id", sa.Integer(), nullable=False),
            sa.Column("definition", sa.String(), nullable=True),
            sa.Column("modifiedAt", sa.DateTime(), nullable=False),
            sa.Column("modifiedBy", sa.Uuid(), nullable=False),
            sa.Column("modifiedByName", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("workspace_id"),
        )

    if not insp.has_table("workspaces_imagery"):
        op.create_table(
            "workspaces_imagery",
            sa.Column("workspace_id", sa.Integer(), nullable=False),
            sa.Column(
                "definition",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column("modifiedAt", sa.DateTime(), nullable=False),
            sa.Column("modifiedBy", sa.Uuid(), nullable=False),
            sa.Column("modifiedByName", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"]),
            sa.PrimaryKeyConstraint("workspace_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    assert bind is not None
    insp = inspect(bind)

    if insp.has_table("workspaces_imagery"):
        op.drop_table("workspaces_imagery")
    if insp.has_table("workspaces_long_quests"):
        op.drop_table("workspaces_long_quests")
    if insp.has_table("workspaces"):
        op.drop_table("workspaces")
