from __future__ import annotations

from uuid import UUID

import pytest

from api.core.security import (
    TdeiDataset,
    TdeiProjectGroupRole,
    UserInfo,
    UserInfoPGMembership,
)
from api.src.users.schemas import WorkspaceUserRoleType
from api.src.workspaces.schemas import WorkspaceType

PG = "00000000-0000-0000-0000-000000000001"


def _user(*, osm_roles=None, pg_roles=None, accessible=None):
    u = UserInfo()
    u.credentials = "x"
    u.user_uuid = UUID("11111111-1111-1111-1111-111111111111")
    u.user_name = "test"
    u.osmWorkspaceRoles = osm_roles or {}
    u.projectGroups = (
        [
            UserInfoPGMembership(
                project_group_name="PG",
                project_group_id=PG,
                tdeiRoles=pg_roles or [TdeiProjectGroupRole.MEMBER],
            )
        ]
        if pg_roles is not None or accessible is not None
        else []
    )
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


Role = TdeiProjectGroupRole
WsType = WorkspaceType


class TestCanCreateWorkspace:
    @pytest.mark.parametrize("ws_type", list(WorkspaceType))
    def test_member_only_cannot_create(self, ws_type):
        """A user whose only TDEI role in the PG is member cannot create any type."""
        u = _user(pg_roles=[Role.MEMBER])
        assert u.canCreateWorkspace(PG, ws_type) is False

    @pytest.mark.parametrize(
        "role, ws_type, allowed",
        [
            (Role.POINT_OF_CONTACT, WsType.OSW, True),
            (Role.POINT_OF_CONTACT, WsType.PATHWAYS, True),
            (Role.POINT_OF_CONTACT, WsType.FLEX, True),
            (Role.OSW_GENERATOR, WsType.OSW, True),
            (Role.OSW_GENERATOR, WsType.PATHWAYS, False),
            (Role.OSW_GENERATOR, WsType.FLEX, False),
            (Role.PATHWAYS_GENERATOR, WsType.OSW, False),
            (Role.PATHWAYS_GENERATOR, WsType.PATHWAYS, True),
            (Role.PATHWAYS_GENERATOR, WsType.FLEX, False),
            (Role.FLEX_GENERATOR, WsType.OSW, False),
            (Role.FLEX_GENERATOR, WsType.PATHWAYS, False),
            (Role.FLEX_GENERATOR, WsType.FLEX, True),
        ],
    )
    def test_role_limits_workspace_type(self, role, ws_type, allowed):
        """Each role creates only the types it covers, alone or alongside member."""
        assert _user(pg_roles=[role]).canCreateWorkspace(PG, ws_type) is allowed
        assert (
            _user(pg_roles=[Role.MEMBER, role]).canCreateWorkspace(PG, ws_type)
            is allowed
        )

    def test_roles_combine_across_types(self):
        """OSW + Pathways generator roles together can create both types."""
        u = _user(pg_roles=[Role.OSW_GENERATOR, Role.PATHWAYS_GENERATOR])
        assert u.canCreateWorkspace(PG, WsType.OSW) is True
        assert u.canCreateWorkspace(PG, WsType.PATHWAYS) is True

    def test_other_project_group_cannot_create(self):
        """A creator role in one PG does not allow creating in another."""
        u = _user(pg_roles=[Role.POINT_OF_CONTACT])
        other_pg = "00000000-0000-0000-0000-000000000099"
        assert u.canCreateWorkspace(other_pg, WsType.OSW) is False


OTHER_PG = "00000000-0000-0000-0000-000000000099"


def _dataset(project_group_id: str, status: str) -> TdeiDataset:
    return TdeiDataset(
        tdei_dataset_id="33333333-3333-3333-3333-333333333333",
        project_group_id=project_group_id,
        status=status,
        data_type="osw",
    )


class TestCanCreateWorkspaceFromDataset:
    @pytest.mark.parametrize(
        "dataset_pg, status, allowed",
        [
            (PG, "Pre-Release", True),  # unreleased, in the target PG
            (PG, "Publish", True),
            (OTHER_PG, "Publish", True),  # released, from any PG
            (OTHER_PG, "Pre-Release", False),  # unreleased, from another PG
        ],
    )
    def test_release_status_and_owning_pg(self, dataset_pg, status, allowed):
        """POC of the target PG: released from anywhere, unreleased only its own."""
        u = _user(pg_roles=[Role.POINT_OF_CONTACT])
        dataset = _dataset(dataset_pg, status)
        assert u.canCreateWorkspaceFromDataset(PG, WsType.OSW, dataset) is allowed

    def test_generator_follows_the_same_dataset_rule(self):
        """Data generators keep their type limit and also get the dataset rule."""
        u = _user(pg_roles=[Role.OSW_GENERATOR])
        released_elsewhere = _dataset(OTHER_PG, "Publish")
        unreleased_elsewhere = _dataset(OTHER_PG, "Pre-Release")
        assert u.canCreateWorkspaceFromDataset(PG, WsType.OSW, released_elsewhere)
        assert not u.canCreateWorkspaceFromDataset(PG, WsType.OSW, unreleased_elsewhere)
        assert not u.canCreateWorkspaceFromDataset(
            PG, WsType.PATHWAYS, released_elsewhere
        )

    @pytest.mark.parametrize("status", ["Publish", "Pre-Release"])
    def test_member_only_cannot_create_from_any_dataset(self, status):
        """Case 2: member-only in the PG never creates, whatever the dataset."""
        u = _user(pg_roles=[Role.MEMBER])
        assert (
            u.canCreateWorkspaceFromDataset(PG, WsType.OSW, _dataset(PG, status))
            is False
        )

    def test_poc_elsewhere_cannot_pull_unreleased_dataset_in(self):
        """Case 1: member of the dataset's PG + POC of another PG cannot open
        the dataset's unreleased data in either PG."""
        u = _user(pg_roles=[Role.POINT_OF_CONTACT])
        u.projectGroups.append(
            UserInfoPGMembership(
                project_group_name="Other",
                project_group_id=OTHER_PG,
                tdeiRoles=[Role.MEMBER],
            )
        )
        unreleased_in_other = _dataset(OTHER_PG, "Pre-Release")
        assert not u.canCreateWorkspaceFromDataset(PG, WsType.OSW, unreleased_in_other)
        assert not u.canCreateWorkspaceFromDataset(
            OTHER_PG, WsType.OSW, unreleased_in_other
        )

    def test_unreleased_dataset_pg_matches_regardless_of_case(self):
        """TDEI may not send the canonical lowercase hyphenated UUID."""
        u = _user(pg_roles=[Role.POINT_OF_CONTACT])
        dataset = _dataset(PG.upper().replace("-", ""), "Pre-Release")
        assert u.canCreateWorkspaceFromDataset(PG, WsType.OSW, dataset) is True


class TestCanCreateWorkspaceIdFormat:
    def test_project_group_id_matches_regardless_of_case_or_hyphens(self):
        u = _user(pg_roles=[Role.POINT_OF_CONTACT])
        assert u.canCreateWorkspace(PG.upper(), WsType.OSW) is True
        assert u.canCreateWorkspace(PG.replace("-", ""), WsType.OSW) is True
