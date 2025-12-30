from enum import Enum, IntEnum

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    UUID,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Unicode,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from api.core.database import Base

#
# These are the schema definitions for the database ORM, NOT DTOs used by the APIs
#
class ExternalAppsDefinitionType(IntEnum):
    NONE = 0
    PUBLIC = 1
    PROJECT_GROUP = 2
class Workspace(Base):
    """Workspaces"""

    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True)
    type = Column(Unicode, nullable=False)

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
        SmallInteger, nullable=False, default=ExternalAppsDefinitionType.NONE.value
    )

    kartaViewToken = Column(Unicode)

    longFormQuestDef: Mapped[list["WorkspaceLongQuest"]] = relationship(
        "WorkspaceLongQuest", uselist=False, lazy="joined", cascade="all, delete"
    )

    imageryListDef: Mapped[list["WorkspaceImagery"]] = relationship(
        "WorkspaceImagery", uselist=False, lazy="joined", cascade="all, delete"
    )
class QuestDefinitionType(IntEnum):
    NONE = 0
    JSON = 1
    URL = 2

class WorkspaceLongQuest(Base):
    """Stores mobile app quest definitions for a workspace"""

    __tablename__ = "workspaces_long_quests"

    workspace_id = Column(Integer, ForeignKey(Workspace.id), primary_key=True)

    definition = Column(Unicode, nullable=True, default=None)
    type = Column(Integer, nullable=False, default=QuestDefinitionType.NONE.value)
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

    definition = Column(JSON, nullable=True, default=None)

    modifiedAt = Column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )
    modifiedBy = Column(UUID(as_uuid=True), nullable=False)
    modifiedByName = Column(Unicode, nullable=False)
