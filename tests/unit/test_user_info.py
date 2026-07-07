"""Unit tests for UserInfo permission logic (pure, no app or DB)."""

from api.core.security import WorkspaceUserRoleType
from tests.support import factories


def test_get_project_group_ids():
    user = factories.make_user_info(project_group_ids=["pg-a", "pg-b"])
    assert user.getProjectGroupIds() == ["pg-a", "pg-b"]


def test_is_workspace_lead_via_osm_role():
    user = factories.make_user_info(
        osm_workspace_roles={5: [WorkspaceUserRoleType.LEAD]}
    )
    assert user.isWorkspaceLead(5) is True
    assert user.isWorkspaceLead(6) is False


def test_is_workspace_lead_via_poc_group():
    # A point-of-contact in a group that owns workspace 9 is a lead there.
    user = factories.make_user_info(
        project_group_ids=["pg-owner"],
        poc_group_ids=("pg-owner",),
        accessible_workspace_ids={"pg-owner": [9]},
    )
    assert user.isWorkspaceLead(9) is True
    assert user.isWorkspaceLead(10) is False


def test_poc_without_workspace_ownership_is_not_lead():
    user = factories.make_user_info(
        project_group_ids=["pg-owner"],
        poc_group_ids=("pg-owner",),
        accessible_workspace_ids={"pg-owner": []},
    )
    assert user.isWorkspaceLead(9) is False


def test_is_workspace_validator():
    user = factories.make_user_info(
        osm_workspace_roles={3: [WorkspaceUserRoleType.VALIDATOR]}
    )
    assert user.isWorkspaceValidator(3) is True
    assert user.isWorkspaceValidator(4) is False


def test_is_workspace_contributor():
    user = factories.make_user_info(
        accessible_workspace_ids={"pg-a": [1, 2], "pg-b": [3]}
    )
    assert user.isWorkspaceContributor(2) is True
    assert user.isWorkspaceContributor(3) is True
    assert user.isWorkspaceContributor(99) is False
