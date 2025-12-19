from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token
from api.src.workspaces.models import WorkspaceCreate, WorkspaceLongQuestBase, WorkspaceLongQuestUpdate, WorkspaceResponse, WorkspaceUpdate
from api.src.workspaces.repository import WorkspaceRepository

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
        newWorkspaceRecord = await service.get_workspace(current_user.projectGroups, workspace_id)
        newWorkspaceRecord.model_copy(update=workspace_data.model_dump(exclude_unset=True))

        workspace = await service.update_workspace(
            current_user.projectGroups, workspace_id, newWorkspaceRecord
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


@router.get("/{workspace_id}/quests/long", response_model=WorkspaceResponse)
async def get_long_quest(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuestBase | None:
    try:
        workspace = await service.get_workspace(
            current_user.projectGroups, workspace_id
        )
        return workspace.longFormQuestDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


@router.get("/{workspace_id}/quests/long/settings", response_model=WorkspaceLongQuestBase)
async def get_long_quest_settings(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuestBase | None:
    try:
        workspace = await service.get_workspace(
            current_user.projectGroups, workspace_id
        )

        if(workspace.longFormQuestDef is None):
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return workspace.longFormQuestDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise

@router.patch("/{workspace_id}/quests/long/settings", response_model=WorkspaceLongQuestBase)
async def update_long_quest_settings(
    workspace_id: int,
    longform_quest_data: WorkspaceLongQuestUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuestBase | None:
    try:
        workspace:WorkspaceUpdate = await service.get_workspace(
            current_user.projectGroups, workspace_id
        ) # type: ignore
        
        update_data = longform_quest_data.model_dump(exclude_unset=True)
        
        if(workspace.longFormQuestDef is not None):
            workspace.longFormQuestDef.model_copy(update=update_data)

        updatedWorkspace = await service.update_workspace(
            current_user.projectGroups, workspace_id, workspace
        )
        return updatedWorkspace.longFormQuestDef
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise
