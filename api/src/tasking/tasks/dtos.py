from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import Field as PydField

from api.src.tasking.projects.dtos import Pagination, WireModel
from api.src.tasking.tasks.schemas import FeedbackReason, TaskStatus

# ---------------------------------------------------------------------------
# Task boundary GeoJSON (input for /tasks/validate and /tasks/save)
# ---------------------------------------------------------------------------


class TaskBoundaryPolygon(WireModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]


class TaskBoundaryFeature(WireModel):
    type: Literal["Feature"]
    geometry: TaskBoundaryPolygon
    properties: Optional[dict[str, Any]] = None


class TaskBoundariesFeatureCollection(WireModel):
    type: Literal["FeatureCollection"]
    features: list[TaskBoundaryFeature] = PydField(min_length=1)


GridSource = Literal["grid", "import"]


# ---------------------------------------------------------------------------
# Task detail / list
# ---------------------------------------------------------------------------


class TaskLockSummary(WireModel):
    user_id: UUID
    user_name: Optional[str] = None
    locked_at: datetime
    expires_at: datetime


class LastMapper(WireModel):
    user_id: UUID
    user_name: Optional[str] = None


class TaskResponse(WireModel):
    id: int
    task_number: int
    status: TaskStatus
    geometry: TaskBoundaryPolygon
    area_sqkm: float
    lock: Optional[TaskLockSummary] = None
    last_mapper: Optional[LastMapper] = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(WireModel):
    tasks: list[TaskResponse]
    pagination: Pagination


# ---------------------------------------------------------------------------
# Validate / Save
# ---------------------------------------------------------------------------


class ValidateWarning(WireModel):
    task_index: int
    issue: Literal["polygon_exceeds_grid_size"]
    area_sqkm: Optional[float] = None


class ValidatePreviewResponse(WireModel):
    valid: bool
    warnings: list[ValidateWarning] = PydField(default_factory=list)
    source: GridSource = "import"
    feature_collection: TaskBoundariesFeatureCollection


class SaveTasksRequest(WireModel):
    source: GridSource
    feature_collection: TaskBoundariesFeatureCollection


class SaveTasksResponse(WireModel):
    project_id: int
    task_boundary_type: GridSource
    task_count: int
    tasks: list[TaskResponse]
    idempotency_key: Optional[str] = None
    replayed: bool = False


# ---------------------------------------------------------------------------
# Submit / lock
# ---------------------------------------------------------------------------


class FeedbackInput(WireModel):
    reason_category: Optional[FeedbackReason] = None
    notes: str = PydField(min_length=1, max_length=4000)


class SubmitRequest(WireModel):
    # osm_changeset_id: int = PydField(ge=1)
    done: bool
    feedback: Optional[FeedbackInput] = None


class SubmitTaskChangeset(WireModel):
    osm_changeset_id: int = PydField(ge=1)


class SubmitTaskChangesetResponse(WireModel):
    osm_changeset_id: int = PydField(ge=1)
    task_number: int
    project_id: int
    workspace_id: int
    inserted_id: Optional[int] = (
        None  # ID of the newly inserted changeset row, if applicable
    )


class ExistingLockSummary(WireModel):
    task_number: int
    task_status: TaskStatus
    locked_at: datetime
    expires_at: datetime


__all__ = [
    "ExistingLockSummary",
    "FeedbackInput",
    "GridSource",
    "LastMapper",
    "SaveTasksRequest",
    "SaveTasksResponse",
    "SubmitRequest",
    "TaskBoundariesFeatureCollection",
    "TaskBoundaryFeature",
    "TaskBoundaryPolygon",
    "TaskListResponse",
    "TaskLockSummary",
    "TaskResponse",
    "ValidatePreviewResponse",
    "ValidateWarning",
]
