"""Tests for api/core/security.py.

Covers the @test comments on the module / UserInfo / validate_token:
- the permission structure matches CLAUDE.md
- UserInfo methods return correct values for given PG/workspace roles
- attributes are populated correctly from JWT + TDEI + DB data
- network failures are handled gracefully (typed HTTP errors, no false success)
- the user-info cache works and evicts on token rotation / explicit eviction
"""

from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlmodel.ext.asyncio.session import AsyncSession

import api.core.security as sec
from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes

USER_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def clear_cache():
    sec._user_info_cache.clear()
    yield
    sec._user_info_cache.clear()


def _creds(token="tok"):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _FakeResp:
    def __init__(self, status_code=200, data=None, raises=False):
        self.status_code = status_code
        self._data = data
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("bad json")
        return self._data


class _FakeTdeiClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def get(self, *args, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._resp


# --- CLAUDE.md permission structure ---------------------------------------


def test_permission_structure_matches_claude_md():
    # POC ("Project Group Admin") -> lead on workspaces the group owns.
    poc = factories.make_user_info(
        project_group_ids=["pg"],
        poc_group_ids=("pg",),
        accessible_workspace_ids={"pg": [1]},
    )
    assert poc.isWorkspaceLead(1) is True

    # Lead granted via Workspaces setting (an OSM-DB role).
    lead = factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.LEAD]}
    )
    assert lead.effective_role(1) == WorkspaceUserRoleType.LEAD

    # Validator granted via Workspaces setting.
    validator = factories.make_user_info(
        osm_workspace_roles={1: [WorkspaceUserRoleType.VALIDATOR]}
    )
    assert validator.effective_role(1) == WorkspaceUserRoleType.VALIDATOR

    # Contributor implied by project-group membership.
    contributor = factories.make_user_info(accessible_workspace_ids={"pg": [1]})
    assert contributor.isWorkspaceContributor(1) is True
    assert contributor.effective_role(1) == WorkspaceUserRoleType.CONTRIBUTOR


def test_effective_role_precedence_lead_over_validator():
    user = factories.make_user_info(
        osm_workspace_roles={
            1: [WorkspaceUserRoleType.VALIDATOR, WorkspaceUserRoleType.LEAD]
        }
    )
    assert user.effective_role(1) == WorkspaceUserRoleType.LEAD


def test_tdei_role_enum_values():
    assert sec.TdeiProjectGroupRole.POINT_OF_CONTACT == "poc"
    assert sec.TdeiProjectGroupRole.MEMBER == "member"


# --- validate_token: success + attribute population ------------------------


async def _run_validate(monkeypatch, *, payload, tdei, task, osm):
    monkeypatch.setattr(sec, "validate_and_decode_token", lambda _t: payload)
    monkeypatch.setattr(sec, "_tdei_client", tdei)
    return await sec.validate_token(
        _creds(),
        cast(AsyncSession, osm),
        cast(AsyncSession, task),
    )


async def test_validate_token_populates_attributes(monkeypatch):
    pg_id = "pg-1"
    tdei = _FakeTdeiClient(
        _FakeResp(
            200,
            [
                {
                    "tdei_project_group_id": pg_id,
                    "project_group_name": "PG One",
                    "roles": ["poc"],
                }
            ],
        )
    )
    task = fakes.FakeSession(fakes.mappings({"tdeiProjectGroupId": pg_id, "id": 5}))
    osm = fakes.FakeSession(fakes.mappings({"workspace_id": 5, "role": "lead"}))

    info = await _run_validate(
        monkeypatch,
        payload={"sub": USER_ID, "jti": "j1", "preferred_username": "alice"},
        tdei=tdei,
        task=task,
        osm=osm,
    )

    assert info.user_uuid == UUID(USER_ID)
    assert info.user_name == "alice"
    assert info.getProjectGroupIds() == [pg_id]
    assert info.accessibleWorkspaceIds == {pg_id: [5]}
    assert info.osmWorkspaceRoles == {5: ["lead"]}
    assert info.isWorkspaceLead(5) is True


# --- validate_token: graceful failure handling -----------------------------


async def test_malformed_token_returns_401(monkeypatch):
    def boom(_t):
        raise ValueError("bad token")

    monkeypatch.setattr(sec, "validate_and_decode_token", boom)
    with pytest.raises(HTTPException) as exc:
        await sec.validate_token(
            _creds(),
            cast(AsyncSession, fakes.FakeSession()),
            cast(AsyncSession, fakes.FakeSession()),
        )
    assert exc.value.status_code == 401


async def test_missing_sub_returns_401(monkeypatch):
    monkeypatch.setattr(sec, "validate_and_decode_token", lambda _t: {"jti": "j"})
    with pytest.raises(HTTPException) as exc:
        await sec.validate_token(
            _creds(),
            cast(AsyncSession, fakes.FakeSession()),
            cast(AsyncSession, fakes.FakeSession()),
        )
    assert exc.value.status_code == 401


async def test_invalid_uuid_sub_returns_401(monkeypatch):
    monkeypatch.setattr(
        sec, "validate_and_decode_token", lambda _t: {"sub": "not-a-uuid"}
    )
    with pytest.raises(HTTPException) as exc:
        await sec.validate_token(
            _creds(),
            cast(AsyncSession, fakes.FakeSession()),
            cast(AsyncSession, fakes.FakeSession()),
        )
    assert exc.value.status_code == 401


async def test_tdei_network_error_returns_502(monkeypatch):
    tdei = _FakeTdeiClient(exc=httpx.ConnectError("down"))
    with pytest.raises(HTTPException) as exc:
        await _run_validate(
            monkeypatch,
            payload={"sub": USER_ID, "jti": "j"},
            tdei=tdei,
            task=fakes.FakeSession(),
            osm=fakes.FakeSession(),
        )
    assert exc.value.status_code == 502


async def test_tdei_non_200_returns_401(monkeypatch):
    tdei = _FakeTdeiClient(_FakeResp(403, None))
    with pytest.raises(HTTPException) as exc:
        await _run_validate(
            monkeypatch,
            payload={"sub": USER_ID, "jti": "j"},
            tdei=tdei,
            task=fakes.FakeSession(),
            osm=fakes.FakeSession(),
        )
    assert exc.value.status_code == 401


async def test_tdei_bad_json_returns_401(monkeypatch):
    tdei = _FakeTdeiClient(_FakeResp(200, raises=True))
    with pytest.raises(HTTPException) as exc:
        await _run_validate(
            monkeypatch,
            payload={"sub": USER_ID, "jti": "j"},
            tdei=tdei,
            task=fakes.FakeSession(),
            osm=fakes.FakeSession(),
        )
    assert exc.value.status_code == 401


async def test_uninitialized_tdei_client_returns_503(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await _run_validate(
            monkeypatch,
            payload={"sub": USER_ID, "jti": "j"},
            tdei=None,
            task=fakes.FakeSession(),
            osm=fakes.FakeSession(),
        )
    assert exc.value.status_code == 503


# --- caching ---------------------------------------------------------------


async def test_cache_hit_skips_refetch(monkeypatch):
    pg_id = "pg-1"

    def fresh_tdei():
        return _FakeTdeiClient(
            _FakeResp(
                200,
                [
                    {
                        "tdei_project_group_id": pg_id,
                        "project_group_name": "PG",
                        "roles": ["member"],
                    }
                ],
            )
        )

    # First call populates the cache.
    await _run_validate(
        monkeypatch,
        payload={"sub": USER_ID, "jti": "j1"},
        tdei=fresh_tdei(),
        task=fakes.FakeSession(fakes.mappings()),
        osm=fakes.FakeSession(fakes.mappings()),
    )

    # Second call with the same jti must NOT hit TDEI again: a raising client
    # proves the cached entry is returned without a refetch.
    monkeypatch.setattr(
        sec, "_tdei_client", _FakeTdeiClient(exc=httpx.ConnectError("x"))
    )
    monkeypatch.setattr(
        sec, "validate_and_decode_token", lambda _t: {"sub": USER_ID, "jti": "j1"}
    )
    info = await sec.validate_token(
        _creds(),
        cast(AsyncSession, fakes.FakeSession()),
        cast(AsyncSession, fakes.FakeSession()),
    )
    assert info.user_uuid == UUID(USER_ID)


async def test_cache_evicts_on_token_rotation(monkeypatch):
    pg_id = "pg-1"

    def tdei_with_role(role):
        return _FakeTdeiClient(
            _FakeResp(
                200,
                [
                    {
                        "tdei_project_group_id": pg_id,
                        "project_group_name": "PG",
                        "roles": [role],
                    }
                ],
            )
        )

    await _run_validate(
        monkeypatch,
        payload={"sub": USER_ID, "jti": "j1"},
        tdei=tdei_with_role("member"),
        task=fakes.FakeSession(fakes.mappings()),
        osm=fakes.FakeSession(fakes.mappings()),
    )

    # New jti -> stale entry evicted, fresh fetch performed.
    info = await _run_validate(
        monkeypatch,
        payload={"sub": USER_ID, "jti": "j2"},
        tdei=tdei_with_role("poc"),
        task=fakes.FakeSession(fakes.mappings()),
        osm=fakes.FakeSession(fakes.mappings()),
    )
    assert sec.TdeiProjectGroupRole.POINT_OF_CONTACT in info.projectGroups[0].tdeiRoles


def test_evict_user_from_cache_removes_entry():
    uid = UUID(USER_ID)
    sentinel = factories.make_user_info(user_id=USER_ID)
    sec._user_info_cache[uid] = sentinel
    assert uid in sec._user_info_cache

    sec.evict_user_from_cache(uid)
    assert uid not in sec._user_info_cache

    # Evicting an absent key is a no-op (no KeyError).
    sec.evict_user_from_cache(uid)
