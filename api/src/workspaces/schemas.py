from enum import IntEnum, StrEnum

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    UUID,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    TypeDecorator,
    Unicode,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from api.core.database import Base


#
# These are the schema definitions for the database ORM, NOT DTOs used by the APIs
#
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
    FLEX  = "flex"

class Workspace(Base):
    """Workspaces"""

    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True)
    type = Column(StrEnumType(WorkspaceType), nullable=False)

    title = Column(Unicode, nullable=False)
    description = Column(Unicode)

    tdeiProjectGroupId = Column(UUID(as_uuid=True), nullable=False)
    tdeiRecordId = Column(UUID(as_uuid=True))
    tdeiServiceId = Column(UUID(as_uuid=True))

    tdeiMetadata = Column(JSON)

    createdAt = Column(DateTime, nullable=False, default=func.now())
    createdBy = Column(UUID(as_uuid=True), nullable=False)
    createdByName = Column(Unicode, nullable=False)

    geometry = Column(Geometry("MULTIPOLYGON", srid=4326))

    externalAppAccess = Column(
        IntEnumType(ExternalAppsDefinitionType), nullable=False, default=ExternalAppsDefinitionType.NONE
    )

    kartaViewToken = Column(Unicode)

    longFormQuestDef: Mapped[list["WorkspaceLongQuest"]] = relationship(
        "WorkspaceLongQuest", uselist=False, lazy="joined", cascade="all, delete"
    )

    imageryListDef: Mapped[list["WorkspaceImagery"]] = relationship(
        "WorkspaceImagery", uselist=False, lazy="joined", cascade="all, delete-orphan"
    )


class QuestDefinitionType(IntEnum):
    NONE = 0
    JSON = 1
    URL = 2

class WorkspaceLongQuest(Base):
    """Stores mobile app quest definitions for a workspace"""

    __tablename__ = "workspaces_long_quests"

    workspace_id = Column(Integer, ForeignKey(Workspace.id), primary_key=True)

    type = Column(IntEnumType(QuestDefinitionType), nullable=False, default=QuestDefinitionType.NONE)

    definition = Column(Unicode, nullable=True, default=None)
    url = Column(Unicode, nullable=True, default=None)

    modifiedAt = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    modifiedBy = Column(UUID(as_uuid=True), nullable=False)
    modifiedByName = Column(Unicode, nullable=False)


class WorkspaceImagery(Base):
    """Stores imagery list for a workspace"""

    __tablename__ = "workspaces_imagery"

    workspace_id = Column(Integer, ForeignKey(Workspace.id), primary_key=True)

    definition = Column(JSON, nullable=False, default=None)

    modifiedAt = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    modifiedBy = Column(UUID(as_uuid=True), nullable=False)
    modifiedByName = Column(Unicode, nullable=False)


class WorkspaceUserRoleType(StrEnum):
    LEAD = "lead"
    VALIDATOR = "validator"
    CONTRIBUTOR = "contributor"

class WorkspaceUserRole(Base):
    """Associates users with workspaces and their roles"""

    __tablename__ = "user_workspace_roles"

    user_id = Column(UUID(as_uuid=True), primary_key=True)
    workspace_id = Column(Integer, ForeignKey(Workspace.id), primary_key=True)

    role = Column(StrEnumType(WorkspaceUserRoleType), nullable=False)
