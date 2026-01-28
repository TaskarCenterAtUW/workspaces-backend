from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token
from api.src.workspaces.models import (
    WorkspaceCreate,
    WorkspaceImageryResponse,
    WorkspaceImageryUpdate,
    WorkspaceLongQuestBase,
    WorkspaceLongQuestResponse,
    WorkspaceLongQuestUpdate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from api.src.workspaces.repository import OSMRepository, WorkspaceRepository
from api.src.workspaces.service import OSMService, WorkspaceService

# FIXME: make these consistent with response codes etc?

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


def get_workspace_service(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceService:
    repository = WorkspaceRepository(session)
    return WorkspaceService(repository)

def get_osm_service(
    session: AsyncSession = Depends(get_osm_session),
) -> OSMService:
    repository = OSMRepository(session)
    return OSMService(repository)


# Returns list of workspaces user has access to as JSON payload on success--returns empty JSON list if none
@router.get("/mine", response_model=list[WorkspaceResponse])
async def get_my_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> list[WorkspaceResponse]:
    try:
        workspaces = await service.get_all_workspaces(current_user)
        return workspaces
    except Exception as e:
        logger.error(f"Failed to fetch workspaces: {str(e)}")
        raise


# Returns JSON payload or 204 if not found
@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.get_workspace(current_user, workspace_id)

        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return workspace
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


@router.get("/{workspace_id}/bbox", response_model=None)
async def get_workspace_bbox(
    workspace_id: int,
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    osm_service: OSMService = Depends(get_osm_service),
    current_user: UserInfo = Depends(validate_token),
):
    try:
        workspace = await workspace_service.get_workspace(current_user, workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        result = await osm_service.get_workspace_bbox(current_user, workspace_id)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 201 on success? FIXME? Make consistent with all other methods?
@router.post("/", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.create_workspace(current_user, workspace_data)
        return workspace
    except Exception as e:
        logger.error(f"Failed to create workspace: {str(e)}")
        raise


# Returns the updated workspace on success. FIXME? Make consistent with all other methods?
@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: int,
    workspace_data: WorkspaceUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await service.update_workspace(
            current_user, workspace_id, workspace_data
        )
        return workspace
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await service.delete_workspace(current_user, workspace_id)
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {str(e)}")
        raise


# Returns JSON payload or 204 if not set
@router.get("/{workspace_id}/quests/long", response_model=WorkspaceResponse)
async def get_long_quest(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuestBase | None:
    try:
        workspace = await service.get_workspace(current_user, workspace_id)

        if workspace.longFormQuestDef is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return workspace.longFormQuestDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns JSON payload or 204 if not set
@router.get(
    "/{workspace_id}/quests/long/settings", response_model=WorkspaceLongQuestResponse
)
async def get_long_quest_settings(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuestBase | None:
    try:
        workspace = await service.get_workspace(current_user, workspace_id)

        if workspace.longFormQuestDef is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return workspace.longFormQuestDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.patch(
    "/{workspace_id}/quests/long/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_long_quest_settings(
    workspace_id: int,
    long_quest_data: WorkspaceLongQuestUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await service.set_longform_quest(current_user, workspace_id, long_quest_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# Returns JSON payload or 204 if not set
@router.get("/{workspace_id}/imagery/settings", response_model=WorkspaceImageryResponse)
async def get_imagery_settings(
    workspace_id: int,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceImageryResponse | None:
    try:
        workspace = await service.get_workspace(current_user, workspace_id)

        if workspace.imageryListDef is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return workspace.imageryListDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.patch(
    "/{workspace_id}/imagery/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_imagery_settings(
    workspace_id: int,
    imagery_data: WorkspaceImageryUpdate,
    service: WorkspaceService = Depends(get_workspace_service),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await service.set_imagery(current_user, workspace_id, imagery_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise
