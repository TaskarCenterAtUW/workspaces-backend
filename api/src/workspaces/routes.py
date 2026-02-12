from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.logging import get_logger
from api.core.security import UserInfo, evict_user_from_cache, validate_token
from api.src.users.repository import UserRepository
from api.src.users.schemas import WorkspaceUserRoleType
from api.src.workspaces.repository import OSMRepository, WorkspaceRepository
from api.src.workspaces.schemas import (
    ImagerySettingsPatch,
    QuestDefinitionTypeName,
    QuestSettingsPatch,
    QuestSettingsResponse,
    Workspace,
    WorkspaceCreate,
    WorkspaceImagery,
    WorkspacePatch,
    WorkspaceResponse,
)

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])



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


def get_user_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> UserRepository:
    return UserRepository(session)


# Returns list of workspaces user has access to as JSON payload on success--returns empty JSON list if none
@router.get("/mine", response_model=list[WorkspaceResponse])
async def get_my_workspaces(
    repository: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> list[WorkspaceResponse]:
    try:
        workspaces = await repository.getAll(current_user)
        return [WorkspaceResponse.from_workspace(ws, current_user) for ws in workspaces]
    except Exception as e:
        logger.error(f"Failed to fetch workspaces: {str(e)}")
        raise


# Returns JSON payload or 204 if not found
@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)

        if workspace is None:
            raise HTTPException(
                status_code=status.HTTP_204_NO_CONTENT,
                detail="No Content",
            )

        return WorkspaceResponse.from_workspace(workspace, current_user)
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
        await repository_ws.getById(current_user, workspace_id)
        bbox = await repository_osm.getWorkspaceBBox(current_user, workspace_id)
        return bbox
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 201 on success?
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_users: UserRepository = Depends(get_user_repository),
    current_user: UserInfo = Depends(validate_token),
) -> dict[str, int]:
    try:
        workspace = await repository_ws.create(current_user, workspace_data)

        # Assign the creator as lead so that non-POC members can manage their
        # own workspace:
        #
        await repository_users.assign_member_role(
            workspace.id,
            current_user.user_uuid,
            WorkspaceUserRoleType.LEAD,
        )

        # Evict the creator's cache so their next request reflects the new
        # workspace and lead role rather than serving stale data for up to
        # an hour:
        #
        evict_user_from_cache(current_user.user_uuid)

        return {"workspaceId": workspace.id}
    except Exception as e:
        logger.error(f"Failed to create workspace: {str(e)}")
        raise


# Returns the updated workspace on success.
@router.patch("/{workspace_id}", response_model=Workspace)
async def update_workspace(
    workspace_id: int,
    workspace_data: WorkspacePatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Workspace:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to update this workspace",
        )

    try:
        workspace = await repository_ws.update(
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
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_users: UserRepository = Depends(get_user_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to delete this workspace",
        )

    try:
        members = await repository_users.get_privileged_workspace_members(workspace_id)
        await repository_ws.delete(current_user, workspace_id)
        await repository_users.remove_all_member_roles(workspace_id)
        for member in members:
            evict_user_from_cache(UUID(member.auth_uid))
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {str(e)}")
        raise


# QUESTS


# Returns JSON payload or 204 if not set
@router.get(
    "/{workspace_id}/quests/long/settings", response_model=QuestSettingsResponse
)
async def get_long_quest_settings(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> QuestSettingsResponse:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)
        quest = workspace.longFormQuestDef

        if quest is None:
            return QuestSettingsResponse(
                workspace_id=workspace_id,
                type=QuestDefinitionTypeName.NONE,
                definition=None,
                url=None,
                modified_at=workspace.createdAt,
                modified_by=workspace.createdBy,
                modified_by_name="",
            )

        return QuestSettingsResponse(
            workspace_id=quest.workspace_id,
            type=QuestDefinitionTypeName(quest.type.name),
            definition=quest.definition,
            url=quest.url,
            modified_at=quest.modifiedAt,
            modified_by=quest.modifiedBy,
            modified_by_name=quest.modifiedByName,
        )
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# Returns 204 on success
@router.patch(
    "/{workspace_id}/quests/long/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_long_quest_settings(
    workspace_id: int,
    long_quest_data: QuestSettingsPatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )

    try:
        await repository_ws.save_longform_quest(
            current_user, workspace_id, long_quest_data
        )
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# IMAGERY


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
    imagery_data: ImagerySettingsPatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )

    try:
        await repository_ws.save_imagery_def(current_user, workspace_id, imagery_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise
