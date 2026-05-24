from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.security import UserInfo, validate_token
from api.src.tasking.projects.repository import TaskingProjectRepository
from api.src.tasking.projects.dtos import (
    AoiFeature,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from api.src.tasking.projects.schemas import (
    AoiInput,
    ProjectStatus,
)
from api.src.workspaces.repository import WorkspaceRepository

router = APIRouter(
    prefix="/workspaces/{workspace_id}/tasking/projects",
    tags=["tasking-projects"],
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_project_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> TaskingProjectRepository:
    return TaskingProjectRepository(session)


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


def assert_workspace_lead(workspace_id: int, current_user: UserInfo) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )


# ---------------------------------------------------------------------------
# Projects — CRUD + lifecycle
# ---------------------------------------------------------------------------


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    workspace_id: int,
    status_filter: Annotated[
        ProjectStatus | None, Query(alias="status")
    ] = None,
    text_search: str | None = Query(default=None, max_length=255),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    order_by: str = Query("created_at"),
    order_by_type: str = Query("DESC"),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await project_repo.list_projects(
        workspace_id,
        status_filter=status_filter,
        text_search=text_search,
        page=page,
        page_size=page_size,
        order_by=order_by,
        order_dir=order_by_type,
    )


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    workspace_id: int,
    body: ProjectCreateRequest,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.create(workspace_id, current_user, body)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await project_repo.get(workspace_id, project_id)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    workspace_id: int,
    project_id: int,
    body: ProjectUpdateRequest,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.patch(workspace_id, project_id, body)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    await project_repo.soft_delete(workspace_id, project_id)


@router.post("/{project_id}/activate", response_model=ProjectResponse)
async def activate_project(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.activate(workspace_id, project_id)


@router.post("/{project_id}/close", response_model=ProjectResponse)
async def close_project(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.close(workspace_id, project_id)


@router.post("/{project_id}/reset", response_model=ProjectResponse)
async def reset_project(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.reset(workspace_id, project_id)


# ---------------------------------------------------------------------------
# AOI
# ---------------------------------------------------------------------------


@router.get("/{project_id}/aoi", response_model=AoiFeature)
async def get_project_aoi(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    return await project_repo.get_aoi(workspace_id, project_id)


@router.post("/{project_id}/aoi", response_model=AoiFeature)
async def upload_project_aoi(
    workspace_id: int,
    project_id: int,
    body: AoiInput = Body(...),
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    return await project_repo.upload_aoi(workspace_id, project_id, body)


@router.delete("/{project_id}/aoi", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_aoi(
    workspace_id: int,
    project_id: int,
    current_user: UserInfo = Depends(validate_token),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
    project_repo: TaskingProjectRepository = Depends(get_project_repo),
):
    await assert_workspace_visible(workspace_id, current_user, workspace_repo)
    assert_workspace_lead(workspace_id, current_user)
    await project_repo.delete_aoi(workspace_id, project_id)
