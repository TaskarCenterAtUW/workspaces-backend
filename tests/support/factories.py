"""Factories for the domain objects tests need.

These build real ``UserInfo`` / SQLModel instances (not mocks) so the
permission logic and serialization under test run for real. Defaults are
deterministic so tests can assert on concrete values.
"""

from datetime import datetime
from uuid import UUID

from api.core.security import TdeiProjectGroupRole, UserInfo, UserInfoPGMembership
from api.src.teams.schemas import WorkspaceTeam
from api.src.users.schemas import User
from api.src.workspaces.schemas import Workspace, WorkspaceType

# Stable identifiers reused across tests.
DEFAULT_PG_ID = "11111111-1111-1111-1111-111111111111"
DEFAULT_USER_ID = "22222222-2222-2222-2222-222222222222"


def make_user_info(
    *,
    user_id: str = DEFAULT_USER_ID,
    user_name: str = "Test User",
    project_group_ids: list[str] | None = None,
    accessible_workspace_ids: dict[str, list[int]] | None = None,
    osm_workspace_roles: dict[int, list] | None = None,
    poc_group_ids: tuple[str, ...] = (),
) -> UserInfo:
    """Build a ``UserInfo`` the way ``validate_token`` would have.

    ``project_group_ids``        -- TDEI project groups the user belongs to.
    ``accessible_workspace_ids`` -- pg_id -> workspace ids (from the task DB).
    ``osm_workspace_roles``      -- workspace_id -> roles (from the OSM DB).
    ``poc_group_ids``            -- subset of groups where the user is a POC
                                    (grants lead rights on owned workspaces).
    """
    info = UserInfo()
    info.credentials = "test-token"
    info.user_uuid = UUID(user_id)
    info.user_name = user_name

    if project_group_ids is None:
        project_group_ids = [DEFAULT_PG_ID]

    info.projectGroups = [
        UserInfoPGMembership(
            project_group_name=f"PG {pg_id}",
            project_group_id=pg_id,
            tdeiRoles=(
                [TdeiProjectGroupRole.POINT_OF_CONTACT]
                if pg_id in poc_group_ids
                else [TdeiProjectGroupRole.MEMBER]
            ),
        )
        for pg_id in project_group_ids
    ]
    info.accessibleWorkspaceIds = accessible_workspace_ids or {}
    info.osmWorkspaceRoles = osm_workspace_roles or {}
    return info


def make_workspace(
    *,
    id: int | None = 1,
    title: str = "Test Workspace",
    type: WorkspaceType = WorkspaceType.OSW,
    tdei_project_group_id: str = DEFAULT_PG_ID,
    created_by: str = DEFAULT_USER_ID,
    created_by_name: str = "Test User",
    **extra,
) -> Workspace:
    return Workspace(
        id=id,
        title=title,
        type=type,
        tdeiProjectGroupId=UUID(tdei_project_group_id),
        createdBy=UUID(created_by),
        createdByName=created_by_name,
        createdAt=datetime(2026, 1, 1),
        **extra,
    )


def make_user(
    *,
    id: int = 1,
    auth_uid: str = DEFAULT_USER_ID,
    email: str = "user@example.com",
    display_name: str = "User One",
) -> User:
    return User(id=id, auth_uid=auth_uid, email=email, display_name=display_name)


def make_team(
    *,
    id: int = 1,
    name: str = "Team Alpha",
    workspace_id: int = 1,
    users: list[User] | None = None,
) -> WorkspaceTeam:
    team = WorkspaceTeam(id=id, name=name, workspace_id=workspace_id)
    # ``WorkspaceTeamItem.from_team`` reads ``len(team.users)``; set it
    # explicitly since the relationship isn't loaded from a real DB here.
    team.users = users or []
    return team
