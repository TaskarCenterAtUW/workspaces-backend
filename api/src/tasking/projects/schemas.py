from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Optional
from uuid import UUID

from geoalchemy2 import Geometry
from pydantic import BaseModel, Field as PydField
from sqlalchemy import Column, Enum as SAEnum
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

    # Cross-DB reference to workspaces.id; no FK by design, matching
    # the existing `user_workspace_roles` convention.
    workspace_id: int = Field(nullable=False, index=True)

    name: str = Field(max_length=255, nullable=False)
    instructions: Optional[str] = None

    # Bind to the Postgres enum from the migration. `name=` and
    # `values_callable` are required so SQLAlchemy uses the existing
    # `tasking_project_status` type (lowercase values) instead of
    # auto-generating a new one keyed by member names.
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
    "ProjectStatus",
    "TaskBoundaryType",
    "TaskingProject",
    "_Feature",
    "_FeatureCollection",
    "_MultiPolygon",
    "_Polygon",
]
