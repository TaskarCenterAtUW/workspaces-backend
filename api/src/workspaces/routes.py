from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token

from api.src.workspaces.repository import OSMRepository, WorkspaceRepository
from api.src.workspaces.schemas import QuestDefinitionType, Workspace, WorkspaceImagery, WorkspaceLongQuest, WorkspaceUserRoleType

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


def get_workspace_repository(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    repository = WorkspaceRepository(session)
    return repository

def get_osm_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> OSMRepository:
    repository = OSMRepository(session)
    return repository


# Returns list of workspaces user has access to as JSON payload on success--returns empty JSON list if none
@router.get("/mine", response_model=list[Workspace])
async def get_my_workspaces(
    repository: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> list[Workspace]:
    try:
        workspaces = await repository.getAll(current_user)
        return workspaces
    except Exception as e:
        logger.error(f"Failed to fetch workspaces: {str(e)}")
        raise


# Returns JSON payload or 204 if not found
@router.get("/{workspace_id}", response_model=Workspace)
async def get_workspace(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Workspace:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)

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
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_osm: OSMRepository = Depends(get_osm_repository),
    current_user: UserInfo = Depends(validate_token),
):
    try:
        # this first query is for permissions checking
        workspace = await repository_ws.getById(current_user, workspace_id)
        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        bbox = await repository_osm.getWorkspaceBBox(current_user, workspace_id)

        if bbox is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 201 on success? 
@router.post("/", response_model=Workspace, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: dict[str, Any],
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Workspace:
    try:
        workspace = await repository_ws.create(current_user, workspace_data)
        return workspace
    except Exception as e:
        logger.error(f"Failed to create workspace: {str(e)}")
        raise


# Returns the updated workspace on success. 
@router.patch("/{workspace_id}", response_model=Workspace)
async def update_workspace(
    workspace_id: int,
    workspace_data: dict[str, Any],
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Workspace:
    try:
        workspace = await repository_ws.update(current_user, workspace_id, workspace_data)
        return workspace
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await repository_ws.delete(current_user, workspace_id)
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {str(e)}")
        raise


### QUESTS

# Returns JSON payload or 204 if not set
@router.get(
    "/{workspace_id}/quests/long/settings", response_model=WorkspaceLongQuest
)
async def get_long_quest_settings(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceLongQuest | None:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)

        if workspace.longFormQuestDef is None:
            return WorkspaceLongQuest(
                workspace_id=workspace_id,
                type = QuestDefinitionType.NONE,    
                definition = None,
                url = None,
                modifiedAt=workspace.createdAt,
                modifiedBy=workspace.createdBy,
                modifiedByName="",
            )
        else:
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
    long_quest_data: dict[str, Any],
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await repository_ws.updateLongformQuest(current_user, workspace_id, long_quest_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


### IMAGERY

# Returns JSON payload or 204 if not set
@router.get("/{workspace_id}/imagery/settings")
async def get_imagery_settings(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceImagery | None:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)

        if workspace.imageryListDef is None:
            return WorkspaceImagery(
                workspace_id=workspace_id, 
                definition=[],
                modifiedAt=workspace.createdAt,
                modifiedBy=workspace.createdBy,
                modifiedByName="",
            )
        else:
            return WorkspaceImagery(**workspace.imageryListDef.model_dump())
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.patch(
    "/{workspace_id}/imagery/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_imagery_settings(
    workspace_id: int,
    imagery_data: dict[str, Any],
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    try:
        await repository_ws.updateImageryDef(current_user, workspace_id, imagery_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


### USERS

@router.get("/{workspace_id}/users")
async def get_users(
    workspace_id: int,
    current_user: UserInfo = Depends(validate_token),
    repository_osm: OSMRepository = Depends(get_osm_repository),
):
    return await repository_osm.getAllUsers()


@router.post("/{workspace_id}/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_user_with_role(
    workspace_id: int,
    user_id: UUID,   
    role: WorkspaceUserRoleType,

    current_user: UserInfo = Depends(validate_token),
    repository_osm: OSMRepository = Depends(get_osm_repository),

):
    return await repository_osm.addUserToWorkspaceWithRole(current_user, workspace_id, user_id, role)

@router.delete("/{workspace_id}/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_with_role(
    workspace_id: int,
    user_id: UUID,   
    current_user: UserInfo = Depends(validate_token),
    repository_osm: OSMRepository = Depends(get_osm_repository),
):
    return await repository_osm.removeUserFromWorkspace(current_user, workspace_id, user_id)