"""Tests for the OSM token bridge in ``api/core/security.py``.

``_bridge_token_to_osm`` mirrors a validated TDEI token into the OSM database's
``oauth_access_tokens`` table so osm-rails (doorkeeper) and cgimap authenticate
it through their standard OAuth2 path. We cover:

- it is a no-op unless ``WS_OSM_OAUTH_APPLICATION_ID`` is configured,
- when enabled it provisions the ``users`` row and inserts a plaintext
  ``oauth_access_tokens`` row owned by that user, with ``expires_in`` derived
  from the JWT ``exp``,
- a DB failure never propagates (auth must not break) and rolls back.
"""

import time
from typing import cast
from uuid import uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

import api.core.security as security
from api.core.config import settings


class RecordingSession:
    """Async session stand-in that records (sql, params) and returns a user id
    row for the ``SELECT id FROM users`` lookup."""

    def __init__(self, user_id=99):
        self.calls: list[tuple[str, dict]] = []
        self.commits = 0
        self.rollbacks = 0
        self._user_id = user_id

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

        if "SELECT id FROM users" in sql:
            return _R([(self._user_id,)])
        return _R([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def sql_for(self, needle):
        return [c for c in self.calls if needle in c[0]]


async def test_bridge_is_noop_when_disabled(monkeypatch):
    """With WS_OSM_OAUTH_APPLICATION_ID == 0 the bridge touches no DB."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 0)
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


async def test_bridge_provisions_user_and_mirrors_token(monkeypatch):
    """When enabled: users upsert -> id lookup -> oauth_access_tokens upsert."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 7)
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_SCOPES", "write_api read_prefs")
    uid = uuid4()
    exp = int(time.time()) + 3600
    session = RecordingSession(user_id=42)

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uid,
        user_name="alice",
        email="alice@example.com",
        token="jwt-token",
        exp=exp,
    )

    # users row provisioned idempotently, keyed by auth_uid = str(sub)
    user_inserts = session.sql_for("INSERT INTO users")
    assert user_inserts and "ON CONFLICT (auth_uid)" in user_inserts[0][0]
    assert user_inserts[0][1]["auth_uid"] == str(uid)
    assert user_inserts[0][1]["email"] == "alice@example.com"

    # token mirrored with the configured application/scopes and exp-derived TTL
    token_inserts = session.sql_for("oauth_access_tokens")
    assert token_inserts and "ON CONFLICT (token)" in token_inserts[0][0]
    # Re-presenting a token must reactivate it (clear revocation, refresh expiry).
    assert "DO UPDATE" in token_inserts[0][0]
    assert "revoked_at = NULL" in token_inserts[0][0]
    params = token_inserts[0][1]
    assert params["app_id"] == 7
    assert params["user_id"] == 42
    assert params["token"] == "jwt-token"
    assert params["scopes"] == "write_api read_prefs"
    assert 3590 <= params["expires_in"] <= 3600

    assert session.commits == 1
    assert session.rollbacks == 0


async def test_bridge_expires_in_none_without_exp(monkeypatch):
    """A token without an `exp` claim mirrors with a NULL expires_in."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 7)
    session = RecordingSession()

    await security._bridge_token_to_osm(
        cast(AsyncSession, session),
        user_uuid=uuid4(),
        user_name="alice",
        email=None,
        token="jwt-token",
        exp=None,
    )

    params = session.sql_for("oauth_access_tokens")[0][1]
    assert params["expires_in"] is None


async def test_bridge_is_best_effort_on_db_error(monkeypatch):
    """A DB failure must not propagate out of validation; it rolls back."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 7)

    class BoomSession(RecordingSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("db down")

    session = BoomSession()

    # Must not raise.
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
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 0)
    session = RecordingSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "old-token")

    assert session.calls == []
    assert session.commits == 0


async def test_revoke_marks_only_the_superseded_token(monkeypatch):
    """Revocation flips revoked_at for exactly the given token, if not already."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 7)
    session = RecordingSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "old-token")

    updates = session.sql_for("UPDATE oauth_access_tokens")
    assert updates and "revoked_at" in updates[0][0]
    assert "WHERE token = :token AND revoked_at IS NULL" in updates[0][0]
    assert updates[0][1]["token"] == "old-token"
    assert session.commits == 1


async def test_revoke_is_best_effort_on_db_error(monkeypatch):
    """A DB failure during revocation must not propagate; it rolls back."""
    monkeypatch.setattr(settings, "WS_OSM_OAUTH_APPLICATION_ID", 7)

    class BoomSession(RecordingSession):
        async def execute(self, statement, params=None):
            raise RuntimeError("db down")

    session = BoomSession()

    await security._revoke_osm_token(cast(AsyncSession, session), "tok")

    assert session.rollbacks == 1
    assert session.commits == 0
