

from __future__ import annotations

from uuid import UUID

from api.core.security import (
    TdeiProjectGroupRole,
    UserInfo,
    UserInfoPGMembership,
)
from api.src.users.schemas import WorkspaceUserRoleType


PG = "00000000-0000-0000-0000-000000000001"


def _user(*, osm_roles=None, pg_roles=None, accessible=None):
    u = UserInfo()
    u.credentials = "x"
    u.user_uuid = UUID("11111111-1111-1111-1111-111111111111")
    u.user_name = "test"
    u.osmWorkspaceRoles = osm_roles or {}
    u.projectGroups = [
        UserInfoPGMembership(
            project_group_name="PG",
            project_group_id=PG,
            tdeiRoles=pg_roles or [TdeiProjectGroupRole.MEMBER],
        )
    ] if pg_roles is not None or accessible is not None else []
    u.accessibleWorkspaceIds = accessible or {}
    return u


class TestIsWorkspaceLead:
    def test_explicit_lead_role(self):
        """User with WorkspaceUserRoleType.LEAD on the workspace is a lead."""
        u = _user(osm_roles={42: [WorkspaceUserRoleType.LEAD]})
        assert u.isWorkspaceLead(42) is True

    def test_poc_in_owning_pg_grants_lead(self):
        """TDEI POINT_OF_CONTACT in the project group that owns the workspace implies lead."""
        u = _user(
            pg_roles=[TdeiProjectGroupRole.POINT_OF_CONTACT],
            accessible={PG: [42]},
        )
        assert u.isWorkspaceLead(42) is True

    def test_member_only_is_not_lead(self):
        """A plain TDEI MEMBER (no POC, no explicit LEAD) is NOT a lead."""
        u = _user(
            pg_roles=[TdeiProjectGroupRole.MEMBER],
            accessible={PG: [42]},
        )
        assert u.isWorkspaceLead(42) is False

    def test_no_membership_is_not_lead(self):
        """A user with no project-group membership at all is NOT a lead."""
        assert _user().isWorkspaceLead(42) is False


class TestIsWorkspaceValidator:
    def test_explicit_validator_role(self):
        """User with WorkspaceUserRoleType.VALIDATOR on the workspace is a validator."""
        u = _user(osm_roles={42: [WorkspaceUserRoleType.VALIDATOR]})
        assert u.isWorkspaceValidator(42) is True

    def test_lead_is_not_implicit_validator(self):
        """LEAD does NOT implicitly grant VALIDATOR — distinct roles by design."""
        u = _user(osm_roles={42: [WorkspaceUserRoleType.LEAD]})
        assert u.isWorkspaceValidator(42) is False


class TestIsWorkspaceContributor:
    def test_any_pg_membership_is_contributor(self):
        """Any project-group membership that owns the workspace makes the user a contributor."""
        u = _user(
            pg_roles=[TdeiProjectGroupRole.MEMBER],
            accessible={PG: [42]},
        )
        assert u.isWorkspaceContributor(42) is True

    def test_outsider_is_not_contributor(self):
        """An outsider (no PG membership) is NOT a contributor."""
        assert _user().isWorkspaceContributor(42) is False
