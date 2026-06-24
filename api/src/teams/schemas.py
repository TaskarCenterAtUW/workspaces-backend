from typing import TYPE_CHECKING, Self

from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from api.src.users.schemas import User

# @test: Test that this class matches the Alembic-defined database schema 
# @test: Test that the foreign key references are correct and that the relationships are correctly defined
# @test: Test that any values from Python and properly serialized to the database and that any values from the database are properly deserialized to Python
# @test: Test that any serialization/deserialization doesn't lose any precision or data 
class WorkspaceTeamUser(SQLModel, table=True):
    """Team to User link table"""

    __tablename__ = "team_user" # type: ignore[assignment]

    team_id: int | None = Field(default=None, primary_key=True, foreign_key="teams.id")
    user_id: int | None = Field(default=None, primary_key=True, foreign_key="users.id")


class WorkspaceTeamBase(SQLModel):
    """Shared fields for workspace teams"""

    name: str = Field(min_length=1)

# @test: Test that this class matches the Alembic-defined database schema 
# @test: Test that the foreign key references are correct and that the relationships are correctly defined
# @test: Test that any values from Python and properly serialized to the database and that any values from the database are properly deserialized to Python
# @test: Test that any serialization/deserialization doesn't lose any precision or data 
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
        assert team.id is not None  # persisted team always has an id
        return cls(id=team.id, name=team.name, member_count=len(team.users))


class WorkspaceTeamCreate(WorkspaceTeamBase):
    """New workspace team DTO"""

    pass


class WorkspaceTeamUpdate(WorkspaceTeamBase):
    """Modify workspace team DTO"""

    pass
