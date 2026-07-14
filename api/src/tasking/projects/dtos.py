from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField
from pydantic import field_validator

from api.src.tasking.projects.schemas import (
    AoiInput,
    ProjectRoleType,
    ProjectStatus,
    TaskBoundaryType,
    _MultiPolygon,
)

# ---------------------------------------------------------------------------
# Shared DTO base.
# ---------------------------------------------------------------------------


class WireModel(BaseModel):

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Project DTOs
# ---------------------------------------------------------------------------


class ProjectRoleAssignment(WireModel):
    """Seed entry for `tasking_project_roles` at create time."""

    user_id: UUID
    role: Literal["lead", "validator", "contributor"]


class ProjectCreateRequest(WireModel):
    """Body for `POST /workspaces/{wid}/tasking/projects`."""

    name: str = PydField(min_length=1, max_length=255)
    instructions: Optional[str] = PydField(default=None, max_length=10_000)
    review_required: bool = True
    lock_timeout_hours: int = PydField(default=8, ge=1, le=720)
    aoi: Optional[AoiInput] = None
    role_assignments: list[ProjectRoleAssignment] = PydField(default_factory=list)
    custom_imagery: Optional[dict[str, Any]] = PydField(default=None)
    description: Optional[str] = PydField(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class ProjectUpdateRequest(WireModel):
    """Body for `PATCH /workspaces/{wid}/tasking/projects/{pid}`.

    All fields optional; per-status mutability is enforced in the
    repository layer.
    """

    name: Optional[str] = PydField(default=None, min_length=1, max_length=255)
    instructions: Optional[str] = PydField(default=None, max_length=10_000)
    lock_timeout_hours: Optional[int] = PydField(default=None, ge=1, le=720)
    review_required: Optional[bool] = None
    custom_imagery: Optional[dict[str, Any]] = PydField(default=None)
    description: Optional[str] = PydField(default=None, max_length=10_000)


class ProjectResponse(WireModel):
    """Project detail returned by GET/POST/PATCH/activate/close/reset."""

    id: int
    workspace_id: int
    name: str
    instructions: Optional[str] = None
    status: ProjectStatus
    review_required: bool
    lock_timeout_hours: int
    task_boundary_type: Optional[TaskBoundaryType] = None
    has_aoi: bool
    task_count: int
    created_by: UUID
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    custom_imagery: Optional[Any] = None
    description: Optional[str] = None


class ProjectListItem(WireModel):
    """Row for `GET /workspaces/{wid}/tasking/projects`."""

    id: int
    name: str
    status: ProjectStatus
    task_count: int
    percent_completed: int
    created_by: UUID
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class Pagination(WireModel):
    page: int
    page_size: int
    total: int


class ProjectListResponse(WireModel):
    results: list[ProjectListItem]
    pagination: Pagination


class ProjectNameValidationResponse(WireModel):
    exists: bool


# ---------------------------------------------------------------------------
# AOI response shape (canonical Feature wrapping a MultiPolygon)
# ---------------------------------------------------------------------------


class AoiFeature(WireModel):
    type: Literal["Feature"] = "Feature"
    geometry: _MultiPolygon
    properties: dict[str, Any] = PydField(default_factory=dict)


# ---------------------------------------------------------------------------
# Project-role management DTOs
# ---------------------------------------------------------------------------


class ProjectRoleItem(WireModel):
    """One row of `GET /projects/{pid}/roles`."""

    user_id: UUID
    user_name: Optional[str] = None
    role: ProjectRoleType
    updated_at: datetime


class ProjectRoleListResponse(WireModel):
    """Paginated-but-flat list of role assignments for a project."""

    results: list[ProjectRoleItem]


class ProjectRoleAddRequest(WireModel):
    """Body for `POST /projects/{pid}/roles`."""

    user_id: UUID
    role: ProjectRoleType


class ProjectRoleUpdateRequest(WireModel):
    """Body for `PATCH /projects/{pid}/roles/{user_id}`."""

    role: ProjectRoleType


# ---------------------------------------------------------------------------
# Self project-roles — `GET /me/workspaces/{wid}/tasking/projects/roles`.
# One row per project in the workspace with the caller's effective role on
# that project: explicit `tasking_project_roles` row if present, else the
# workspace-level role (which is the implicit fallback).
# ---------------------------------------------------------------------------


class SelfProjectRoleItem(WireModel):
    project_id: int
    project_name: str
    project_status: ProjectStatus
    role: ProjectRoleType


class SelfProjectRolesResponse(WireModel):
    workspace_role: ProjectRoleType
    projects: list[SelfProjectRoleItem]


__all__ = [
    "AoiFeature",
    "Pagination",
    "ProjectCreateRequest",
    "ProjectListItem",
    "ProjectListResponse",
    "ProjectNameValidationResponse",
    "ProjectResponse",
    "ProjectRoleAddRequest",
    "ProjectRoleAssignment",
    "ProjectRoleItem",
    "ProjectRoleListResponse",
    "ProjectRoleUpdateRequest",
    "ProjectUpdateRequest",
    "SelfProjectRoleItem",
    "SelfProjectRolesResponse",
    "WireModel",
]
