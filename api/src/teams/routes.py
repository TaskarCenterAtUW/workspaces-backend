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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace member of any level
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly calls the repository to fetch the team from the database
# @test: Test that this method properly handles inputs that match the schema in WorkspaceTeamItem

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly calls the repository to add the team to the database
# @test: Test that this method properly handles inputs that match the schema in WorkspaceTeamCreate

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace member of any level
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly calls the repository to fetch the team from the database
# @test: Test that this method properly handles inputs that match the schema in WorkspaceTeamItem

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team 
# @test: Test that this endpoint properly calls the repository to remove the team from the workspace
# @test: Test that this method properly handles inputs that match the schema in WorkspaceTeamUpdate
# @test: Test that this method properly calls the repo to update the team 

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team 
# @test: Test that this endpoint properly calls the repository to remove the team from the workspace

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a member team
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the team is not associated with the workspace
# @test: Test that this endpoint properly handles the case where the user is not a member of the workspace passed
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team passed
# @test: Test that this endpoint properly calls the repository to fetch the users of the team

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


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a member of the workspace via the team
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the user is not a member of the workspace passed
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team passed
# @test: Test that this endpoint properly calls the repository to add the user to the team

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the user does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team 
# @test: Test that this endpoint doesn't allow changing teams the user doesn't have workspace lead permissions for, or users not already associated with the workspace
# @test: Test that this endpoint properly calls the repository to add the user to the team

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

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that the endpoint properly validates that the team needs to be associated with this workspace and errors if not
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the team does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the user does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles the case where the user is not associated with the team 
# @test: Test that this endpoint properly handles the case where the workspace is not associated with the team 
# @test: Test that this endpoint doesn't allow changing teams the user doesn't have workspace lead permissions for, or users not already associated with the team
# @test: Test that this endpoint properly calls the repository to remove the user from the team

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
