"""Tests for api/core/security.py.

Covers the @test comments on the module / UserInfo / validate_token:
- the permission structure matches CLAUDE.md
- UserInfo methods return correct values for given PG/workspace roles
- attributes are populated correctly from JWT + TDEI + DB data
- network failures are handled gracefully (typed HTTP errors, no false success)
- the user-info cache works and evicts on token rotation / explicit eviction
"""

from base64 import b64encode
from typing import cast
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException, Request
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


# --- TDEIHTTPBearer: Bearer or Basic ---------------------------------------
#
# Third-party OSM editors commonly cannot set a custom Authorization scheme, so
# the TDEI token is also accepted in the username field of HTTP Basic (which is
# what URI userinfo, `https://<token>@host/`, encodes to). Specified in the
# workspaces-stack nginx.conf, which is not deployed -- the deployed osm-web
# runs lighttpd and does no token mapping, so this layer owns it.


def _request_with_auth(value: str | None, path: str = "/api/0.6/map"):
    """A minimal Starlette request carrying (or omitting) an Authorization header.

    Defaults to a proxied OSM path, where Basic is allowed. Pass an `/api/v1/`
    path to exercise the Bearer-only scoping.
    """
    raw = [] if value is None else [(b"authorization", value.encode())]
    return Request({"type": "http", "headers": raw, "method": "GET", "path": path})


def _basic(username: str, password: str = "") -> str:
    return "Basic " + b64encode(f"{username}:{password}".encode()).decode()


async def test_bearer_token_is_accepted():
    creds = await sec.security(_request_with_auth("Bearer abc.def.ghi"))
    assert creds is not None
    assert creds.credentials == "abc.def.ghi"


async def test_basic_username_is_used_as_the_token():
    creds = await sec.security(_request_with_auth(_basic("abc.def.ghi")))
    assert creds is not None
    assert creds.credentials == "abc.def.ghi"
    # Normalized so everything downstream sees a single shape.
    assert creds.scheme == "Bearer"


async def test_basic_password_is_ignored():
    # Mirrors nginx's `$remote_user`: the token is the username field only.
    creds = await sec.security(_request_with_auth(_basic("the-token", "ignored")))
    assert creds is not None
    assert creds.credentials == "the-token"


async def test_basic_with_empty_username_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth(_basic("", "the-token")))
    assert excinfo.value.status_code == 401
    # Names the specific problem, and where the token belongs.
    assert "password but no username" in excinfo.value.detail
    assert "username" in excinfo.value.detail


async def test_basic_with_undecodable_payload_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth("Basic !!!not-base64!!!"))
    assert excinfo.value.status_code == 401
    assert "base64" in excinfo.value.detail


async def test_basic_with_swapped_fields_says_so():
    """The likely mistake: token in the password box, account name as username."""
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth(_basic("alice", "head.payload.sig")))
    assert excinfo.value.status_code == 401
    assert "swapped" in excinfo.value.detail


async def test_non_jwt_username_is_not_second_guessed():
    """Only a JWT-shaped password triggers the swap hint.

    Otherwise the username is passed through and fails (or not) in the normal
    token validation, so this heuristic never rejects on its own.
    """
    creds = await sec.security(_request_with_auth(_basic("not-a-jwt", "hunter2")))
    assert creds is not None
    assert creds.credentials == "not-a-jwt"


@pytest.mark.parametrize(
    "header",
    [
        "Basic !!!not-base64!!!",
        _basic("", ""),
        _basic("", "the-token"),
        _basic("alice", "head.payload.sig"),
    ],
)
async def test_every_basic_rejection_explains_the_username_convention(header):
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth(header))
    detail = excinfo.value.detail
    assert "username" in detail
    assert "password is ignored" in detail


async def test_missing_authorization_header_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth(None))
    assert excinfo.value.status_code == 401


async def test_unknown_scheme_is_rejected():
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth("Digest deadbeef"))
    assert excinfo.value.status_code == 401


async def test_basic_auth_yields_a_fully_populated_user_info(monkeypatch):
    """Basic auth is not a second-class path for the native /api/v1 routes.

    `security` resolves the token *before* `validate_token` runs, so the JWT is
    decoded and the TDEI/DB lookups happen exactly as they do for a Bearer
    caller. Every authenticated route -- not just the OSM proxy -- therefore
    sees a fully populated UserInfo.
    """
    pg_id = "pg-1"
    payload = {"sub": USER_ID, "jti": "j1", "preferred_username": "alice"}
    decoded_tokens = []

    def _decode(token):
        decoded_tokens.append(token)
        return payload

    monkeypatch.setattr(sec, "validate_and_decode_token", _decode)
    monkeypatch.setattr(
        sec,
        "_tdei_client",
        _FakeTdeiClient(
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
        ),
    )
    task = fakes.FakeSession(fakes.mappings({"tdeiProjectGroupId": pg_id, "id": 5}))
    osm = fakes.FakeSession(fakes.mappings({"workspace_id": 5, "role": "lead"}))

    creds = await sec.security(_request_with_auth(_basic("the.jwt.here")))
    assert creds is not None
    info = await sec.validate_token(
        creds, cast(AsyncSession, osm), cast(AsyncSession, task)
    )

    # The username field was what got JWT-decoded.
    assert decoded_tokens == ["the.jwt.here"]
    # ...and nothing about the resulting UserInfo is degraded.
    assert info.credentials == "the.jwt.here"
    assert info.user_uuid == UUID(USER_ID)
    assert info.user_name == "alice"
    assert info.getProjectGroupIds() == [pg_id]
    assert info.accessibleWorkspaceIds == {pg_id: [5]}
    assert info.osmWorkspaceRoles == {5: ["lead"]}
    assert info.isWorkspaceLead(5) is True


# --- Basic is scoped to the proxied OSM surface ----------------------------
#
# `validate_token` is one shared dependency, so accepting Basic anywhere would
# accept it everywhere -- including the Lead-gated /api/v1 admin endpoints.
# Scoped by path, which our routing decides, rather than by User-Agent, which
# the client chooses and can forge.


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/workspaces",
        "/api/v1/workspaces/1",
        "/api/v1/users/me",
    ],
)
async def test_basic_is_rejected_on_native_api_paths(path):
    with pytest.raises(HTTPException) as excinfo:
        await sec.security(_request_with_auth(_basic("a.valid.jwt"), path=path))
    assert excinfo.value.status_code == 401
    # Says why, rather than a bare "Not authenticated".
    assert "Basic" in excinfo.value.detail
    assert excinfo.value.headers is not None
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "path",
    [
        "/api/0.6/map",
        "/api/0.6/changeset/create",
        "/workspace/1/api/0.6/map",
        "/api/capabilities.json",
    ],
)
async def test_basic_is_accepted_on_proxied_osm_paths(path):
    creds = await sec.security(_request_with_auth(_basic("a.valid.jwt"), path=path))
    assert creds is not None
    assert creds.credentials == "a.valid.jwt"


async def test_bearer_is_accepted_on_native_api_paths():
    # The scoping restricts Basic only; Bearer works everywhere as before.
    creds = await sec.security(
        _request_with_auth("Bearer a.valid.jwt", path="/api/v1/workspaces")
    )
    assert creds is not None
    assert creds.credentials == "a.valid.jwt"
