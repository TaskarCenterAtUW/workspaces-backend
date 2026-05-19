from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.security import UserInfo, validate_token
from api.src.teams.repository import WorkspaceTeamRepository
from api.src.teams.schemas import (
    WorkspaceTeamCreate,
    WorkspaceTeamItem,
    WorkspaceTeamUpdate,
)
from api.src.users.repository import UserRepository
from api.src.users.schemas import User
from api.src.workspaces.repository import WorkspaceRepository

router = APIRouter(prefix="/workspaces/{workspace_id}/teams", tags=["teams"])


def get_workspace_repo(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    repo = WorkspaceRepository(session)
    return repo


def get_user_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> UserRepository:
    repository = UserRepository(session)
    return repository


def get_team_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> WorkspaceTeamRepository:
    repo = WorkspaceTeamRepository(session)
    return repo


@router.get("")
async def get_all_teams_for_workspace(
    workspace_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
) -> list[WorkspaceTeamItem]:
    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    return await team_repo.get_all(workspace_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_team_for_workspace(
    workspace_id: int,
    team: WorkspaceTeamCreate,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
) -> int:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads can create teams",
        )

    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    return await team_repo.create(workspace_id, team)


@router.get("/{team_id}")
async def get_team_for_workspace(
    workspace_id: int,
    team_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceTeamItem:
    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    return await team_repo.get_item(team_id)


@router.put("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_team_for_workspace(
    workspace_id: int,
    team_id: int,
    team: WorkspaceTeamUpdate,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads can update teams",
        )

    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    await team_repo.update(team_id, team)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team_from_workspace(
    workspace_id: int,
    team_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads can delete teams",
        )

    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    await team_repo.delete(team_id)


@router.get("/{team_id}/members")
async def get_members_in_workspace_team(
    workspace_id: int,
    team_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
) -> list[User]:
    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    return await team_repo.get_members(team_id)


@router.post("/{team_id}/members")
async def join_workspace_team(
    workspace_id: int,
    team_id: int,
    workspace_repo=Depends(get_workspace_repo),
    user_repo=Depends(get_user_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
) -> User:
    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    user = await user_repo.get_current_user(current_user)
    await team_repo.add_member(team_id, user.id)
    return user


@router.put("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_member_to_workspace_team(
    workspace_id: int,
    team_id: int,
    user_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads can add team members",
        )

    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    await team_repo.add_member(team_id, user_id)


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member_from_workspace_team(
    workspace_id: int,
    team_id: int,
    user_id: int,
    workspace_repo=Depends(get_workspace_repo),
    team_repo=Depends(get_team_repo),
    current_user: UserInfo = Depends(validate_token),
):
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads can remove team members",
        )

    # Repo guards if workspace doesn't exist or user cannot access:
    await workspace_repo.getById(current_user, workspace_id)
    await team_repo.assert_team_in_workspace(team_id, workspace_id)
    await team_repo.remove_member(team_id, user_id)
