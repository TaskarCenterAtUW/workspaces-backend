from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.security import UserInfo, validate_token
from api.src.tasking.tasks.dtos import (
    SaveTasksRequest,
    SaveTasksResponse,
    SubmitRequest,
    TaskBoundariesFeatureCollection,
    TaskListResponse,
    TaskResponse,
    ValidatePreviewResponse,
)
from api.src.tasking.tasks.repository import TaskingTaskRepository
from api.src.tasking.tasks.schemas import TaskStatus
from api.src.workspaces.repository import WorkspaceRepository

router = APIRouter(
    prefix="/workspaces/{workspace_id}/tasking/projects/{project_id}",
    tags=["tasking-tasks"],
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_task_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> TaskingTaskRepository:
    return TaskingTaskRepository(session)


def get_workspace_repo(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    return WorkspaceRepository(session)


async def assert_workspace_visible(
    workspace_id: int,
    current_user: UserInfo,
    workspace_repo: WorkspaceRepository,
) -> None:
    await workspace_repo.getById(current_user, workspace_id)


def assert_workspace_lead(workspace_id: int, current_user: UserInfo) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )


# ---------------------------------------------------------------------------
# Tasks — validate / save / list / get
# ---------------------------------------------------------------------------


@router.post("/tasks/grid", response_model=TaskBoundariesFeatureCollection)
async def generate_grid(
    workspace_id: int,
    project_id: int,
    cell_size_meters: int = Query(1000, ge=50, le=100_000),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    """Generate a regular grid of square cells over the project AOI.

    LEAD-only preview — does NOT persist. The client posts the same
    FeatureCollection back through `POST /tasks/save` to commit.
    """
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await task_repo.generate_grid(workspace_id, project_id, cell_size_meters)


@router.post("/tasks/validate", response_model=ValidatePreviewResponse)
async def validate_tasks(
    workspace_id: int,
    project_id: int,
    body: TaskBoundariesFeatureCollection = Body(...),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await task_repo.validate(workspace_id, project_id, body)


@router.post("/tasks/save", response_model=SaveTasksResponse)
async def save_tasks(
    workspace_id: int,
    project_id: int,
    body: SaveTasksRequest,
    response: Response,
    idempotency_key: Annotated[
        Optional[str], Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ] = None,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    payload, replayed = await task_repo.save(
        workspace_id, project_id, current_user, body, idempotency_key
    )
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    return payload


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    workspace_id: int,
    project_id: int,
    status_filter: Annotated[Optional[TaskStatus], Query(alias="status")] = None,
    locked_by_user_id: Optional[UUID] = Query(default=None),
    last_mapper_id: Optional[UUID] = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await task_repo.list_tasks(
        workspace_id,
        project_id,
        status_filter=status_filter,
        locked_by_user_id=locked_by_user_id,
        last_mapper_id=last_mapper_id,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_number}", response_model=TaskResponse)
async def get_task(
    workspace_id: int,
    project_id: int,
    task_number: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await task_repo.get_task(workspace_id, project_id, task_number)


# ---------------------------------------------------------------------------
# Locks — acquire / release / extend / reset
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_number}/lock", response_model=TaskResponse)
async def lock_task(
    workspace_id: int,
    project_id: int,
    task_number: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await task_repo.lock_task(
        workspace_id, project_id, task_number, current_user
    )


@router.delete(
    "/tasks/{task_number}/lock",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unlock_task(
    workspace_id: int,
    project_id: int,
    task_number: int,
    force: bool = Query(False),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    await task_repo.unlock_task(
        workspace_id,
        project_id,
        task_number,
        current_user,
        force=force,
    )


@router.post("/tasks/{task_number}/extend", response_model=TaskResponse)
async def extend_lock(
    workspace_id: int,
    project_id: int,
    task_number: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await task_repo.extend_lock(
        workspace_id, project_id, task_number, current_user
    )


@router.post("/tasks/{task_number}/reset", response_model=TaskResponse)
async def reset_task(
    workspace_id: int,
    project_id: int,
    task_number: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await task_repo.reset_task(
        workspace_id, project_id, task_number, current_user
    )


# ---------------------------------------------------------------------------
# Submit — Done? flow
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_number}/submit", response_model=TaskResponse)
async def submit_task(
    workspace_id: int,
    project_id: int,
    task_number: int,
    body: SubmitRequest,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    task_repo: TaskingTaskRepository = Depends(get_task_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await task_repo.submit(
        workspace_id, project_id, task_number, current_user, body
    )
