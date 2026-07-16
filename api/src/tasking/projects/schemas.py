from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional
from uuid import UUID

from geoalchemy2 import Geometry
from pydantic import BaseModel
from pydantic import Field as PydField
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Enums (mirrors of postgres enums in the migration)
# ---------------------------------------------------------------------------


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    DONE = "done"


class TaskBoundaryType(StrEnum):
    GRID = "grid"
    IMPORT = "import"


# ---------------------------------------------------------------------------
# Table model
# ---------------------------------------------------------------------------


class TaskingProject(SQLModel, table=True):
    """Tasking project — lifecycle, AOI, settings."""

    __tablename__ = "tasking_projects"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)

    workspace_id: int = Field(nullable=False, index=True)

    name: str = Field(max_length=255, nullable=False)
    instructions: Optional[str] = None

    status: ProjectStatus = Field(
        default=ProjectStatus.DRAFT,
        sa_column=Column(
            SAEnum(
                ProjectStatus,
                name="tasking_project_status",
                create_type=False,
                values_callable=lambda enum: [m.value for m in enum],
            ),
            nullable=False,
        ),
    )

    review_required: bool = Field(default=True, nullable=False)
    lock_timeout_hours: int = Field(default=8, nullable=False)

    task_boundary_type: Optional[TaskBoundaryType] = Field(
        default=None,
        sa_column=Column(
            SAEnum(
                TaskBoundaryType,
                name="tasking_task_boundary_type",
                create_type=False,
                values_callable=lambda enum: [m.value for m in enum],
            ),
            nullable=True,
        ),
    )

    # PostGIS MultiPolygon in EPSG:4326. Stored as WKB and converted
    # to / from GeoJSON in the repository layer.
    aoi: Optional[Any] = Field(
        default=None,
        sa_column=Column(Geometry(geometry_type="MULTIPOLYGON", srid=4326)),
    )

    created_by: UUID = Field(nullable=False)
    created_by_name: Optional[str] = None

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False},
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False, "onupdate": datetime.now},
    )
    deleted_at: Optional[datetime] = None

    # Custom Imagery data for the project. Stored as JSONB in the database and converted to / from a Pydantic model in the repository layer.
    custom_imagery: Optional[Any] = Field(
        default=None, sa_column=Column(JSONB, nullable=True, default=None)
    )

    description: Optional[str] = Field(default=None, nullable=True)


# ---------------------------------------------------------------------------
# Project role enum + table
# ---------------------------------------------------------------------------


class ProjectRoleType(StrEnum):
    LEAD = "lead"
    VALIDATOR = "validator"
    CONTRIBUTOR = "contributor"


class TaskingProjectRole(SQLModel, table=True):
    """Per-project role override stored in ``tasking_project_roles``.

    Maps a user (`users.auth_uid`) to a role within a single tasking
    project. Created at project-seed time and managed via the
    ``/projects/{pid}/roles`` endpoints thereafter. Cascades on project
    delete; the user FK is intentionally non-cascading so user records
    are not destroyed by project deletions.
    """

    __tablename__ = "tasking_project_roles"  # type: ignore[assignment]

    project_id: int = Field(primary_key=True, nullable=False)
    user_auth_uid: str = Field(primary_key=True, nullable=False)
    role: ProjectRoleType = Field(
        sa_column=Column(
            SAEnum(
                ProjectRoleType,
                name="workspace_role",
                create_type=False,
                values_callable=lambda enum: [m.value for m in enum],
            ),
            nullable=False,
        ),
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False, "onupdate": datetime.now},
    )


# ---------------------------------------------------------------------------
# GeoJSON input shapes — accepted by the AOI endpoints. Polygon inputs
# are upcast to single-member MultiPolygon at the repository layer.
# ---------------------------------------------------------------------------


class _Polygon(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class _MultiPolygon(BaseModel):
    type: Literal["MultiPolygon"]
    coordinates: list[list[list[list[float]]]]


class _Feature(BaseModel):
    type: Literal["Feature"]
    geometry: _Polygon | _MultiPolygon
    properties: Optional[dict[str, Any]] = None


class _FeatureCollection(BaseModel):
    type: Literal["FeatureCollection"]
    features: list[_Feature] = PydField(min_length=1, max_length=1)


AoiInput = _Polygon | _MultiPolygon | _Feature | _FeatureCollection


__all__ = [
    "AoiInput",
    "ProjectRoleType",
    "ProjectStatus",
    "TaskBoundaryType",
    "TaskingProject",
    "TaskingProjectRole",
    "_Feature",
    "_FeatureCollection",
    "_MultiPolygon",
    "_Polygon",
]
