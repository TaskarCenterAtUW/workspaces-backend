from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from geoalchemy2 import Geometry
from sqlalchemy import JSON as SAJson
from sqlalchemy import Column, SmallInteger, TypeDecorator, Unicode
from sqlmodel import Field, Relationship, SQLModel

from api.src.teams.schemas import WorkspaceTeamUser

if TYPE_CHECKING:
    from api.src.teams.schemas import WorkspaceTeam


class IntEnumType(TypeDecorator):
    """Stores IntEnum as integer, returns as enum."""

    impl = SmallInteger
    cache_ok = True

    def __init__(self, enum_class):
        super().__init__()
        self.enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if value is not None:
            return value.value if isinstance(value, self.enum_class) else value
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return self.enum_class(value)
        return value


class StrEnumType(TypeDecorator):
    """Stores StrEnum as string, returns as enum."""

    impl = Unicode
    cache_ok = True

    def __init__(self, enum_class):
        super().__init__()
        self.enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if value is not None:
            return value.value if isinstance(value, self.enum_class) else value
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return self.enum_class(value)
        return value


class ExternalAppsDefinitionType(IntEnum):
    NONE = 0
    PUBLIC = 1
    PROJECT_GROUP = 2


class WorkspaceType(StrEnum):
    OSW = "osw"
    PATHWAYS = "pathways"
    FLEX = "flex"


class QuestDefinitionType(IntEnum):
    NONE = 0
    JSON = 1
    URL = 2


class WorkspaceUserRoleType(StrEnum):
    LEAD = "lead"
    VALIDATOR = "validator"
    CONTRIBUTOR = "contributor"


class WorkspaceLongQuest(SQLModel, table=True):
    """Stores mobile app quest definitions for a workspace"""

    __tablename__ = "workspaces_long_quests"  # type: ignore[assignment]

    workspace_id: int = Field(foreign_key="workspaces.id", primary_key=True)
    type: QuestDefinitionType = Field(
        default=QuestDefinitionType.NONE,
        sa_column=Column(
            IntEnumType(QuestDefinitionType),
            nullable=False,
            default=QuestDefinitionType.NONE,
        ),
    )
    definition: Optional[str] = None
    url: Optional[str] = None

    modifiedAt: datetime = Field(
        sa_column=Column(nullable=False, default=datetime.now, onupdate=datetime.now)
    )
    modifiedBy: UUID
    modifiedByName: str


class WorkspaceImagery(SQLModel, table=True):
    """Stores imagery list for a workspace"""

    __tablename__ = "workspaces_imagery"  # type: ignore[assignment]

    workspace_id: int = Field(foreign_key="workspaces.id", primary_key=True)
    definition: Optional[list[Any]] = Field(
        default=None, sa_column=Column(SAJson, nullable=False)
    )

    modifiedAt: datetime = Field(
        sa_column=Column(nullable=False, default=datetime.now, onupdate=datetime.now)
    )
    modifiedBy: UUID
    modifiedByName: str


class WorkspaceUserRole(SQLModel, table=True):
    """Associates users with workspaces and their roles"""

    __tablename__ = "user_workspace_roles"  # type: ignore[assignment]

    # this is the TDEI auth user UUID, from the token
    auth_user_uid: str = Field(foreign_key="users.auth_uid", primary_key=True)
    workspace_id: int = Field(foreign_key="workspaces.id", primary_key=True)

    role: WorkspaceUserRoleType = Field(
        sa_column=Column(StrEnumType(WorkspaceUserRoleType), nullable=False)
    )


class User(SQLModel, table=True):
    """Users"""

    __tablename__ = "users"  # type: ignore[assignment]

    id: int = Field(default=None, primary_key=True)

    # this is the user ID from the TDEI authentication system
    auth_uid: str = Field(unique=True, index=True)

    email: str = Field(unique=True, index=True)
    display_name: str = Field(nullable=False)

    teams: list["WorkspaceTeam"] = Relationship(
        back_populates="users", link_model=WorkspaceTeamUser
    )


class Workspace(SQLModel, table=True):
    """Workspaces"""

    __tablename__ = "workspaces"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    type: WorkspaceType = Field(
        sa_column=Column(StrEnumType(WorkspaceType), nullable=False)
    )

    title: str
    description: Optional[str] = None

    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID] = None
    tdeiServiceId: Optional[UUID] = None

    tdeiMetadata: Optional[Any] = Field(default=None, sa_column=Column(SAJson))

    createdAt: datetime = Field(sa_column=Column(nullable=False, default=datetime.now))
    createdBy: UUID
    createdByName: str

    geometry: Optional[Any] = Field(
        default=None, sa_column=Column(Geometry("MULTIPOLYGON", srid=4326))
    )

    externalAppAccess: ExternalAppsDefinitionType = Field(
        default=ExternalAppsDefinitionType.NONE,
        sa_column=Column(
            IntEnumType(ExternalAppsDefinitionType),
            nullable=False,
            default=ExternalAppsDefinitionType.NONE,
        ),
    )

    kartaViewToken: Optional[str] = None

    longFormQuestDef: Optional[WorkspaceLongQuest] = Relationship(
        sa_relationship_kwargs={
            "uselist": False,
            "lazy": "joined",
            "cascade": "all, delete",
        }
    )
    imageryListDef: Optional[WorkspaceImagery] = Relationship(
        sa_relationship_kwargs={
            "uselist": False,
            "lazy": "joined",
            "cascade": "all, delete-orphan",
        }
    )
