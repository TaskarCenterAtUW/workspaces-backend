from typing import TYPE_CHECKING
from enum import StrEnum

from sqlalchemy import Column, Enum
from sqlmodel import Field, Relationship, SQLModel

from api.src.teams.schemas import WorkspaceTeamUser

if TYPE_CHECKING:
    from api.src.teams.schemas import WorkspaceTeam


class WorkspaceUserRoleType(StrEnum):
    LEAD = "lead"
    VALIDATOR = "validator"
    CONTRIBUTOR = "contributor"


class WorkspaceUserRole(SQLModel, table=True):
    """Associates users with workspaces and their roles"""

    __tablename__ = "user_workspace_roles"  # type: ignore[assignment]

    # this is the TDEI auth user UUID, from the token
    user_auth_uid: str = Field(foreign_key="users.auth_uid", primary_key=True)
    # workspace_id lives in a different DB (task DB), so no FK constraint here
    workspace_id: int = Field(primary_key=True)

    role: WorkspaceUserRoleType = Field(
        sa_column=Column(
            Enum(
                WorkspaceUserRoleType,
                name="workspace_role",
                create_type=False,
                values_callable=lambda e: [m.value for m in e],
            ),
            nullable=False,
        )
    )


class SetRoleRequest(SQLModel):
    role: WorkspaceUserRoleType


class WorkspaceUserRoleItem(SQLModel):
    """
    User with their workspace role. DTO for use in the context of a particular
    workspace
    """

    id: int
    auth_uid: str
    email: str
    display_name: str
    role: WorkspaceUserRoleType


class User(SQLModel, table=True):
    """Users in the OSM DB"""

    __tablename__ = "users"  # type: ignore[assignment]

    # User ID referred to by parts of the code based on the OSM DB schema:
    id: int = Field(default=None, primary_key=True)

    # Principal ID from the TDEI OIDC gateway ("subject" in an access token).
    # It differs from the TDEI user ID:
    auth_uid: str = Field(unique=True, index=True)

    email: str = Field(unique=True, index=True)
    display_name: str = Field(nullable=False)

    teams: list["WorkspaceTeam"] = Relationship(
        back_populates="users", link_model=WorkspaceTeamUser
    )
