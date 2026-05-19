from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.security import UserInfo, evict_user_from_cache, validate_token
from api.src.users.repository import UserRepository
from api.src.users.schemas import SetRoleRequest, WorkspaceUserRoleItem
from api.src.workspaces.repository import WorkspaceRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/users", tags=["users"])


def get_user_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> UserRepository:
    repository = UserRepository(session)
    return repository


def get_workspace_repo(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    return WorkspaceRepository(session)


@router.get("", response_model=list[WorkspaceUserRoleItem])
async def get_privileged_workspace_members(
    workspace_id: int,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
):
    if not current_user.isWorkspaceContributor(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Project group membership required to view members",
        )

    return await user_repo.get_privileged_workspace_members(workspace_id)


@router.put("/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def assign_member_role(
    workspace_id: int,
    user_id: UUID,
    body: SetRoleRequest,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Must be a workspace owner to assign roles",
        )

    # Ensure that the workspace exists in the tasks DB before we write to the
    # OSM DB. TODO: remove the check when we merge the DBs with the proper FK
    # constraints that enforce referential integrity internally.
    #
    await workspace_repo.getById(current_user, workspace_id)

    await user_repo.assign_member_role(workspace_id, user_id, body.role)
    evict_user_from_cache(user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member_role(
    workspace_id: int,
    user_id: UUID,
    current_user: UserInfo = Depends(validate_token),
    user_repo: UserRepository = Depends(get_user_repo),
    workspace_repo: WorkspaceRepository = Depends(get_workspace_repo),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Must be a workspace owner to remove roles",
        )

    # Ensure that the workspace exists in the tasks DB before we write to the
    # OSM DB. TODO: remove the check when we merge the DBs with the proper FK
    # constraints that enforce referential integrity internally.
    #
    await workspace_repo.getById(current_user, workspace_id)

    await user_repo.remove_member_role(workspace_id, user_id)
    evict_user_from_cache(user_id)
