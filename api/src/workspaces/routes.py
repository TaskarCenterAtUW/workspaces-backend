from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from api.src.workspaces.service import WorkspaceService

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


def get_workspace_service(
    session: AsyncSession = Depends(get_session),
) -> WorkspaceService:
    repository = WorkspaceRepository(session)
    return WorkspaceService(repository)


@router.get("/mine", response_model=list[WorkspaceResponse])
async def get_my_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> list[WorkspaceResponse]:
    try:
        workspaces = await service.get_all_workspaces(current_user.projectGroups)
        return workspaces
    except Exception as e:
        logger.error(f"Failed to fetch workspaces: {str(e)}")
        raise


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.get_workspace(
            current_user.projectGroups, workspace_id
        )
        return workspace
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


@router.post("/", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.create_workspace(
            current_user.projectGroups, workspace_data
        )
        return workspace
    except Exception as e:
        logger.error(f"Failed to create workspace: {str(e)}")
        raise


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: int,
    workspace_data: WorkspaceUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.update_workspace(
            current_user.projectGroups, workspace_id, workspace_data
        )
        return workspace
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await service.delete_workspace(current_user.projectGroups, workspace_id)
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {str(e)}")
        raise
