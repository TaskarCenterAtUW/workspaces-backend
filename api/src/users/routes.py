from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session
from api.core.security import UserInfo, evict_user_from_cache, validate_token
from api.src.users.repository import UserRepository
from api.src.users.schemas import (
    SetRoleRequest,
    WorkspaceUserRoleItem,
    WorkspaceUserRoleType,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/users", tags=["users"])


def get_user_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> UserRepository:
    repository = UserRepository(session)
    return repository


@router.get("", response_model=list[WorkspaceUserRoleItem])
async def get_users(
    workspace_id: int,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks permission to view members of workspace {workspace_id}",
        )

    return await user_repo.getUsersForWorkspace(workspace_id)


@router.put("/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_role(
    workspace_id: int,
    user_id: UUID,
    body: SetRoleRequest,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
):
    if current_user.isWorkspaceLead(workspace_id) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks permission to modify workspace {workspace_id}",
        )

    await user_repo.addUserToWorkspaceWithRole(
        current_user, workspace_id, user_id, body.role
    )
    evict_user_from_cache(str(user_id))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_with_role(
    workspace_id: int,
    user_id: UUID,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
):
    if current_user.isWorkspaceLead(workspace_id) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks permission to modify workspace {workspace_id}",
        )

    await user_repo.removeUserFromWorkspace(current_user, workspace_id, user_id)
    evict_user_from_cache(str(user_id))
