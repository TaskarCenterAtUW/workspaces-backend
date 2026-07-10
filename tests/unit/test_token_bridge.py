"""Tests for the OSM token bridge in ``api/core/security.py``.

``_bridge_token_to_osm`` mirrors a validated TDEI token into the OSM database's
``oauth_access_tokens`` table so osm-rails (doorkeeper) and cgimap authenticate
it through their standard OAuth2 path. It auto-provisions everything it needs:

- a dedicated *system* ``users`` row that owns the doorkeeper application,
- the doorkeeper ``oauth_applications`` row (idempotent by the client uid),
- the caller's ``users`` row (owns the token),
- the plaintext ``oauth_access_tokens`` row (expires_in from the JWT exp).

We cover: the no-op when disabled, the full provisioning chain when enabled,
the wiring of ids between rows, best-effort failure handling, and revocation.
"""

import time
from typing import cast
from uuid import uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

import api.core.security as security
from api.core.config import settings


class RecordingSession:
    """Async session stand-in that records (sql, params) and answers the two id
    lookups: ``users`` (system vs caller, by auth_uid) and ``oauth_applications``."""

    def __init__(self, system_user_id=1, caller_user_id=42, app_id=7):
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self._system_user_id = system_user_id
        self._caller_user_id = caller_user_id
        self._app_id = app_id

    async def execute(self, statement, params=None):
        params = params or {}
        sql = str(statement)
        self.calls.append((sql, params))

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

        if "SELECT id FROM users" in sql:
            if params.get("auth_uid") == settings.WS_OSM_SYSTEM_USER_AUTH_UID:
                return _R([(self._system_user_id,)])
            return _R([(self._caller_user_id,)])
        if "SELECT id FROM oauth_applications" in sql:
            return _R([(self._app_id,)])
        return _R([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def sql_for(self, needle):
        return [c for c in self.calls if needle in c[0]]


async def test_bridge_is_noop_when_disabled(monkeypatch):
    """With the bridge disabled the bridge touches no DB."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", False)
    session = RecordingSession()

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uuid4(),
        user_name="alice",
        email="alice@example.com",
        token="tok",
        exp=None,
    )

    assert session.calls == []
    assert session.commits == 0


async def test_bridge_provisions_app_users_and_token(monkeypatch):
    """Enabled: system user -> application -> caller user -> mirrored token,
    with the ids wired between rows."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", True)
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_CLIENT_UID", "workspaces-backend")
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_SCOPES", "write_api read_prefs")
    uid = uuid4()
    exp = int(time.time()) + 3600
    session = RecordingSession(system_user_id=1, caller_user_id=42, app_id=7)

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uid,
        user_name="alice",
        email="alice@example.com",
        token="jwt-token",
        exp=exp,
    )

    # System user provisioned (owns the app) and caller user provisioned.
    user_inserts = session.sql_for("INSERT INTO users")
    inserted_auth_uids = {c[1]["auth_uid"] for c in user_inserts}
    assert settings.WS_OSM_SYSTEM_USER_AUTH_UID in inserted_auth_uids
    assert str(uid) in inserted_auth_uids

    # Application created idempotently by uid, owned by the system user (id 1).
    app_inserts = session.sql_for("INSERT INTO oauth_applications")
    assert app_inserts and "ON CONFLICT (uid)" in app_inserts[0][0]
    assert app_inserts[0][1]["uid"] == "workspaces-backend"
    assert app_inserts[0][1]["owner_id"] == 1
    assert app_inserts[0][1]["scopes"] == "write_api read_prefs"

    # Token mirrored: app id from the lookup, resource_owner = caller (42),
    # self-healing upsert, exp-derived TTL.
    token_inserts = session.sql_for("oauth_access_tokens")
    assert token_inserts
    sql, params = token_inserts[0]
    assert "DO UPDATE" in sql and "revoked_at = NULL" in sql
    assert params["app_id"] == 7
    assert params["user_id"] == 42
    assert params["token"] == "jwt-token"
    assert params["scopes"] == "write_api read_prefs"
    assert 3590 <= params["expires_in"] <= 3600

    assert session.commits == 1
    assert session.rollbacks == 0


async def test_bridge_synthesises_email_when_absent(monkeypatch):
    """A caller without an email claim still provisions (email is UNIQUE/NOT NULL)."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", True)
    uid = uuid4()
    session = RecordingSession()

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uid,
        user_name="alice",
        email=None,
        token="jwt-token",
        exp=None,
    )

    caller_insert = next(
        c for c in session.sql_for("INSERT INTO users") if c[1]["auth_uid"] == str(uid)
    )
    assert caller_insert[1]["email"] == f"{uid}@tdei.invalid"

    params = session.sql_for("oauth_access_tokens")[0][1]
    assert params["expires_in"] is None


async def test_bridge_is_best_effort_on_db_error(monkeypatch):
    """A DB failure must not propagate out of validation; it rolls back."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", True)

    class BoomSession(RecordingSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("db down")

    session = BoomSession()

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uuid4(),
        user_name="alice",
        email=None,
        token="tok",
        exp=None,
    )

    assert session.rollbacks == 1
    assert session.commits == 0


async def test_revoke_is_noop_when_disabled(monkeypatch):
    """With the bridge off, revoking a rotated token touches no DB."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", False)
    session = RecordingSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "old-token")

    assert session.calls == []
    assert session.commits == 0


async def test_revoke_marks_only_the_superseded_token(monkeypatch):
    """Revocation flips revoked_at for exactly the given token, if not already."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", True)
    session = RecordingSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "old-token")

    updates = session.sql_for("UPDATE oauth_access_tokens")
    assert updates and "revoked_at" in updates[0][0]
    assert "WHERE token = :token AND revoked_at IS NULL" in updates[0][0]
    assert updates[0][1]["token"] == "old-token"
    assert session.commits == 1


async def test_revoke_is_best_effort_on_db_error(monkeypatch):
    """A DB failure during revocation must not propagate; it rolls back."""
    monkeypatch.setattr(settings, "WS_OSM_TOKEN_BRIDGE_ENABLED", True)

    class BoomSession(RecordingSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("db down")

    session = BoomSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "tok")

    assert session.rollbacks == 1
    assert session.commits == 0
