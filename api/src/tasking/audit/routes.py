from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status  # noqa: F401
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.security import UserInfo, validate_token
from api.src.tasking.audit.dtos import AuditEventListResponse
from api.src.tasking.audit.repository import TaskingAuditRepository
from api.src.tasking.audit.schemas import AuditEventType
from api.src.workspaces.repository import WorkspaceRepository


router = APIRouter(
    prefix="/workspaces/{workspace_id}/tasking/projects",
    tags=["tasking-audit"],
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_audit_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> TaskingAuditRepository:
    return TaskingAuditRepository(session)


def get_workspace_repo(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    return WorkspaceRepository(session)


async def assert_workspace_visible(
    workspace_id: int,
    current_user: UserInfo,
    workspace_repo: WorkspaceRepository,
) -> None:
    """Tenancy gate: 404 if the caller's project groups don't own the
    workspace (matches `WorkspaceRepository.getById`'s convention).
    """
    await workspace_repo.getById(current_user, workspace_id)


# ---------------------------------------------------------------------------
# Project-level audit
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/audit",
    response_model=AuditEventListResponse,
)
async def list_project_audit(
    workspace_id: int,
    project_id: int,
    event_type: Optional[AuditEventType] = Query(default=None),
    task_number: Optional[int] = Query(default=None, ge=1),
    actor_user_id: Optional[UUID] = Query(default=None),
    occurred_from: Optional[datetime] = Query(default=None),
    occurred_to: Optional[datetime] = Query(default=None),
    include_deleted: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    order_by_type: str = Query(default="DESC"),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    audit_repo: TaskingAuditRepository = Depends(get_audit_repo),
):
    """Paginated project-level audit listing, newest first."""
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await audit_repo.list_project_events(
        workspace_id,
        project_id,
        event_type=event_type,
        task_number=task_number,
        actor_user_id=actor_user_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
        order_dir=order_by_type,
    )


# ---------------------------------------------------------------------------
# Task-level audit
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}/tasks/{task_number}/audit",
    response_model=AuditEventListResponse,
)
async def list_task_audit(
    workspace_id: int,
    project_id: int,
    task_number: int,
    event_type: Optional[AuditEventType] = Query(default=None),
    actor_user_id: Optional[UUID] = Query(default=None),
    occurred_from: Optional[datetime] = Query(default=None),
    occurred_to: Optional[datetime] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    order_by_type: str = Query(default="DESC"),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    audit_repo: TaskingAuditRepository = Depends(get_audit_repo),
):
    """Paginated audit listing for a single task, newest first."""
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await audit_repo.list_task_events(
        workspace_id,
        project_id,
        task_number,
        event_type=event_type,
        actor_user_id=actor_user_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        page=page,
        page_size=page_size,
        order_dir=order_by_type,
    )


__all__ = ["router"]
