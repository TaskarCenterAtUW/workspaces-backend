import json
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Optional, Self
from uuid import UUID

from fastapi import Form
from geoalchemy2 import Geometry
from pydantic import model_validator
from sqlalchemy import JSON as SAJson
from sqlalchemy import Boolean, Column, SmallInteger, TypeDecorator, Unicode
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


# @test: Test that this class matches the Alembic-defined database schema
# @test: Test that the foreign key references are correct and that the relationships are correctly defined
# @test: Test that any values from Python and properly serialized to the database and that any values from the database are properly deserialized to Python
# @test: Test that any serialization/deserialization doesn't lose any precision or data
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


# @test: Test that this class matches the Alembic-defined database schema
# @test: Test that the foreign key references are correct and that the relationships are correctly defined
# @test: Test that any values from Python and properly serialized to the database and that any values from the database are properly deserialized to Python
# @test: Test that any serialization/deserialization doesn't lose any precision or data
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

    def isTDEIDataset(self) -> bool:
        return self.tdeiRecordId is not None and self.tdeiProjectGroupId is not None

    def isTDEIOSWDataset(self) -> bool:
        return self.type == WorkspaceType.OSW and self.isTDEIDataset()

    def isTDEIPathwaysDataset(self) -> bool:
        return self.type == WorkspaceType.PATHWAYS and self.isTDEIDataset()


class WorkspaceNameCheck(SQLModel):
    """Fields used to check workspace-name availability within a project group."""

    title: str
    tdeiProjectGroupId: UUID


class WorkspaceNameCheckResponse(SQLModel):
    """Availability result for a workspace name within a project group."""

    available: bool


class WorkspaceCreateCheck(SQLModel):
    """Fields used to ask whether the user may create a workspace, before
    submitting the create form. Omit tdeiRecordId for a blank workspace."""

    type: WorkspaceType
    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID] = None


class WorkspaceCreateCheckResponse(SQLModel):
    """Whether creating that workspace would be allowed, and why not."""

    canCreate: bool
    reason: Optional[str] = None


class WorkspaceCreateWithForm(WorkspaceCreate):
    """Fields the client may supply when creating a workspace via form"""

    def isTDEIDataset(self) -> bool:
        return self.tdeiProjectGroupId is not None

    def isTDEIOSWDataset(self) -> bool:
        return self.type == WorkspaceType.OSW and self.isTDEIDataset()

    def isTDEIPathwaysDataset(self) -> bool:
        return self.type == WorkspaceType.PATHWAYS and self.isTDEIDataset()

    @classmethod
    def as_form(
        cls,
        type: WorkspaceType = Form(...),
        title: str = Form(...),
        tdeiProjectGroupId: UUID = Form(...),
        description: Optional[str] = Form(None),
    ) -> "WorkspaceCreateWithForm":
        return cls(
            type=type,
            title=title,
            description=description,
            tdeiProjectGroupId=tdeiProjectGroupId,
        )


class WorkspacePatch(SQLModel):
    """Fields the client may supply when updating a workspace"""

    title: Optional[str] = None
    description: Optional[str] = None
    externalAppAccess: Optional[ExternalAppsDefinitionType] = None
    autoFlagReview: Optional[bool] = None
    overrideConflicts: Optional[bool] = None


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


class WorkspaceSummaryResponse(SQLModel):
    """
    A workspace as listed by ``GET /workspaces/mine``. Includes the effective
    role for the user making the request. Leaves out what only the
    single-workspace GET needs (see `WorkspaceResponse`), above all
    ``tdeiMetadata``, which for a workspace created from a TDEI dataset holds
    the whole dataset record and made the list large.
    """

    id: int
    type: WorkspaceType
    title: str
    description: Optional[str] = None
    tdeiProjectGroupId: UUID
    tdeiRecordId: Optional[UUID] = None
    tdeiServiceId: Optional[UUID] = None
    createdAt: datetime
    createdBy: UUID
    createdByName: str
    updatedAt: datetime
    externalAppAccess: ExternalAppsDefinitionType
    autoFlagReview: bool = False
    overrideConflicts: bool = False
    role: str
    projectsCount: int = 0
    membersCount: int = 0
    importStatus: Optional[str] = None

    # @test: Test that this class serializes the listed fields, including the effective role for the user making the request, and has no tdeiMetadata, kartaViewToken, longFormQuestDef or imageryListDef

    @classmethod
    def _summary_fields(
        cls,
        workspace: "Workspace",
        user: "UserInfo",
        projects_count: int,
        members_count: int,
    ) -> dict[str, Any]:
        assert workspace.id is not None  # persisted workspace always has an id
        return dict(
            id=workspace.id,
            type=workspace.type,
            title=workspace.title,
            description=workspace.description,
            tdeiProjectGroupId=workspace.tdeiProjectGroupId,
            tdeiRecordId=workspace.tdeiRecordId,
            tdeiServiceId=workspace.tdeiServiceId,
            createdAt=workspace.createdAt,
            createdBy=workspace.createdBy,
            createdByName=workspace.createdByName,
            updatedAt=workspace.updatedAt or workspace.createdAt,
            externalAppAccess=workspace.externalAppAccess,
            autoFlagReview=workspace.autoFlagReview,
            overrideConflicts=workspace.overrideConflicts,
            role=user.effective_role(workspace.id),
            projectsCount=projects_count,
            membersCount=members_count,
            importStatus=workspace.importStatus,
        )

    @classmethod
    def from_workspace(
        cls,
        workspace: "Workspace",
        user: "UserInfo",
        *,
        projects_count: int = 0,
        members_count: int = 0,
    ) -> Self:
        return cls(
            **cls._summary_fields(workspace, user, projects_count, members_count)
        )


class WorkspaceResponse(WorkspaceSummaryResponse):
    """
    Workspace serialized for the single-workspace GET: everything in the list,
    plus the TDEI metadata, the KartaView token and the mobile apps'
    definitions.
    """

    tdeiMetadata: Optional[Any] = None
    kartaViewToken: Optional[str] = None
    # Included in single-workspace GET for mobile app consumption. TODO: remove
    # this when the app fetches these from dedicated endpoints:
    longFormQuestDef: Optional[Any] = None
    imageryListDef: Optional[Any] = None

    # @test: Test that this class properly serializes the workspace data for API responses, including the effective role for the user making the request
    # @test: Test that the values are populated in this class match the expected values from the database and that the relationships are correctly serialized

    @classmethod
    def from_workspace(
        cls,
        workspace: "Workspace",
        user: "UserInfo",
        *,
        imagery_list_def: Any = None,
        long_form_quest_def: Any = None,
        projects_count: int = 0,
        members_count: int = 0,
    ) -> Self:
        return cls(
            **cls._summary_fields(workspace, user, projects_count, members_count),
            tdeiMetadata=workspace.tdeiMetadata,
            kartaViewToken=workspace.kartaViewToken,
            imageryListDef=imagery_list_def,
            longFormQuestDef=long_form_quest_def,
        )


# @test: Test that this class matches the Alembic-defined database schema
# @test: Test that the foreign key references are correct and that the relationships are correctly defined
# @test: Test that any values from Python and properly serialized to the database and that any values from the database are properly deserialized to Python
# @test: Test that any serialization/deserialization doesn't lose any precision or data
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

    # Nullable so that adding this column never requires a data backfill: rows
    # written before this column existed simply read back as None, and
    # WorkspaceResponse.from_workspace falls back to createdAt for those.
    updatedAt: Optional[datetime] = Field(
        default=None,
        sa_column=Column(nullable=True, default=datetime.now, onupdate=datetime.now),
    )

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

    autoFlagReview: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )

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

    importStatus: Optional[str] = Field(
        default=None,
        sa_column=Column(Unicode, nullable=True),
    )

    overrideConflicts: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=True, server_default="false"),
    )
