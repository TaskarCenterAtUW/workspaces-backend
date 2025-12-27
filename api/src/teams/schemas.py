from typing import Self, TYPE_CHECKING

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from api.src.workspaces.schemas import User


class WorkspaceTeamUser(SQLModel, table=True):
    """Team to User link table"""

    __tablename__ = "team_user"

    team_id: int | None = Field(default=None, primary_key=True, foreign_key="teams.id")
    user_id: int | None = Field(default=None, primary_key=True, foreign_key="users.id")


class WorkspaceTeamBase(SQLModel):
    """Shared fields for workspace teams"""

    name: str = Field(min_length=1)


class WorkspaceTeam(WorkspaceTeamBase, table=True):
    """Workspace teams"""

    __tablename__ = "teams"  # type: ignore[assignment]

    id: int | None = Field(default=None, primary_key=True)
    workspace_id: int = Field(index=True)

    # TODO: map this to actual users
    users: list["User"] = Relationship(
        back_populates="teams", link_model=WorkspaceTeamUser
    )


class WorkspaceTeamItem(WorkspaceTeamBase):
    """Existing workspace team DTO"""

    id: int
    member_count: int

    @classmethod
    def from_team(cls, team: WorkspaceTeam) -> Self:
        return cls(id=team.id, name=team.name, member_count=len(team.users))


class WorkspaceTeamCreate(WorkspaceTeamBase):
    """New workspace team DTO"""

    pass


class WorkspaceTeamUpdate(WorkspaceTeamBase):
    """Modify workspace team DTO"""

    pass
