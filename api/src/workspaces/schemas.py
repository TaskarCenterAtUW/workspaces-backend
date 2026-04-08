from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Optional, Self
from uuid import UUID

from geoalchemy2 import Geometry
from pydantic import model_validator
from sqlalchemy import JSON as SAJson
from sqlalchemy import Column, SmallInteger, TypeDecorator, Unicode
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from api.core.security import UserInfo


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


class QuestDefinitionTypeName(StrEnum):
    NONE = "NONE"
    JSON = "JSON"
    URL = "URL"


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


class WorkspaceCreate(SQLModel):
    """Fields the client may supply when creating a workspace"""

    type: WorkspaceType
    title: str
    description: Optional[str] = None
    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID] = None
    tdeiServiceId: Optional[UUID] = None
    tdeiMetadata: Optional[Any] = None


class WorkspacePatch(SQLModel):
    """Fields the client may supply when updating a workspace"""

    title: Optional[str] = None
    description: Optional[str] = None
    externalAppAccess: Optional[ExternalAppsDefinitionType] = None
    autoFlagReview: Optional[bool] = None


class QuestSettingsPatch(SQLModel):
    """Fields the client may supply when saving long-form quest settings"""

    type: QuestDefinitionTypeName
    definition: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def validate_quest_settings(self) -> "QuestSettingsPatch":
        if self.type == QuestDefinitionTypeName.NONE:
            if self.definition:
                raise ValueError("'definition' field not allowed when type is NONE.")
            if self.url:
                raise ValueError("'url' field not allowed when type is NONE.")
        elif self.type == QuestDefinitionTypeName.JSON:
            if not self.definition:
                raise ValueError("'definition' is required when type is JSON.")
            if self.url:
                raise ValueError("'url' field not allowed when type is JSON.")
            # Inexpensive early check. Full JSON parse and schema validation
            # must call validate_quest_definition_schema():
            if not self.definition.strip().startswith("{"):
                raise ValueError("'definition' must be a JSON object.")
        elif self.type == QuestDefinitionTypeName.URL:
            if not self.url:
                raise ValueError("'url' is required when type is URL.")
            if self.definition:
                raise ValueError("'definition' field not allowed when type is URL.")

        return self


class QuestSettingsResponse(SQLModel):
    """Quest settings serialized for API responses"""

    workspace_id: int
    type: QuestDefinitionTypeName
    definition: Optional[str] = None
    url: Optional[str] = None
    modified_at: datetime
    modified_by: UUID
    modified_by_name: str


class ImagerySettingsPatch(SQLModel):
    """Fields the client may supply when saving imagery settings"""

    definition: Optional[list[Any]] = None


class WorkspaceResponse(SQLModel):
    """
    Workspace serialized for API responses. Includes the effective role for the
    user making the request.
    """

    id: int
    type: WorkspaceType
    title: str
    description: Optional[str] = None
    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID] = None
    tdeiServiceId: Optional[UUID] = None
    tdeiMetadata: Optional[Any] = None
    createdAt: datetime
    createdBy: UUID
    createdByName: str
    externalAppAccess: ExternalAppsDefinitionType
    kartaViewToken: Optional[str] = None
    autoFlagReview: bool = False
    role: str
    # Included in single-workspace GET for mobile app consumption. TODO: remove
    # this when the app fetches these from dedicated endpoints:
    longFormQuestDef: Optional[Any] = None
    imageryListDef: Optional[Any] = None

    @classmethod
    def from_workspace(
        cls,
        workspace: "Workspace",
        user: "UserInfo",
        *,
        imagery_list_def: Any = None,
        long_form_quest_def: Any = None,
    ) -> Self:
        return cls(
            id=workspace.id,
            type=workspace.type,
            title=workspace.title,
            description=workspace.description,
            tdeiProjectGroupId=workspace.tdeiProjectGroupId,
            tdeiRecordId=workspace.tdeiRecordId,
            tdeiServiceId=workspace.tdeiServiceId,
            tdeiMetadata=workspace.tdeiMetadata,
            createdAt=workspace.createdAt,
            createdBy=workspace.createdBy,
            createdByName=workspace.createdByName,
            externalAppAccess=workspace.externalAppAccess,
            kartaViewToken=workspace.kartaViewToken,
            autoFlagReview=workspace.autoFlagReview,
            role=user.effective_role(workspace.id),
            imageryListDef=imagery_list_def,
            longFormQuestDef=long_form_quest_def,
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
    autoFlagReview: bool = Field(default=False)

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

