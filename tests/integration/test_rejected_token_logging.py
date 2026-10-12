"""Integration tests: a refused bearer token is logged with its reason.

These go through the real ``validate_token`` (no ``login`` fixture), over HTTP,
on a native route and on the OSM proxy. On 2026-09-26 one browser tab on prod
was refused 179,102 times in 44 minutes and nothing said why; these pin that
every refusal now leaves a reason in the log, without the token, and without
flooding it.
"""

import httpx
import jwt as pyjwt
import pytest

import api.core.security as sec
import api.main
from tests.support.http import StreamingMockTransport

REFUSED = "Rejected bearer token"


@pytest.fixture(autouse=True)
def _fresh_rejection_log():
    sec._rejection_log.clear()
    yield
    sec._rejection_log.clear()


@pytest.fixture
def upstream(monkeypatch):
    """An OSM upstream that records whether anything reached it."""
    transport = StreamingMockTransport(
        lambda req: (200, {"content-type": "text/xml"}, b"<osm version='0.6'/>")
    )
    monkeypatch.setattr(
        api.main,
        "_osm_client",
        httpx.AsyncClient(transport=transport, base_url="http://osm-web"),
    )
    return transport


def _refusals(caplog):
    return [r.getMessage() for r in caplog.records if REFUSED in r.getMessage()]


def _expired_jwt():
    return pyjwt.encode(
        {
            "sub": "22222222-2222-2222-2222-222222222222",
            "jti": "jti-expired-1",
            "exp": 1790406000,
            "iat": 1790319600,
        },
        "integration-test-secret-long-enough-for-hs256",
        algorithm="HS256",
    )


async def test_a_lost_token_on_a_native_route_is_logged_as_sent(client, caplog):
    # "null" is what a client sends once it has lost its token. PyJWT rejects
    # it before any key lookup, so this needs no JWKS.
    response = await client.get(
        "/api/v1/workspaces/mine", headers={"Authorization": "Bearer null"}
    )

    assert response.status_code == 401
    (line,) = _refusals(caplog)
    assert "value 'null'" in line
    assert "DecodeError" in line


async def test_a_refused_token_on_the_osm_proxy_is_logged_and_not_forwarded(
    client, caplog, monkeypatch, upstream
):
    def expired(_token):
        raise pyjwt.ExpiredSignatureError("Signature has expired")

    # Stand in for the JWKS-backed verifier, which would reach out to TDEI.
    monkeypatch.setattr(sec, "validate_and_decode_token", expired)
    token = _expired_jwt()

    response = await client.get(
        "/api/0.6/user/details.json",
        headers={"Authorization": f"Bearer {token}", "X-Workspace": "1"},
    )

    assert response.status_code == 401
    assert upstream.last_request is None  # refused here, never proxied
    (line,) = _refusals(caplog)
    assert "ExpiredSignatureError" in line
    assert "jti=jti-expired-1" in line and "exp=2026-09-26T07:00:00Z" in line
    assert token not in line


async def test_a_retry_storm_is_logged_once(client, caplog, monkeypatch, upstream):
    def expired(_token):
        raise pyjwt.ExpiredSignatureError("Signature has expired")

    monkeypatch.setattr(sec, "validate_and_decode_token", expired)
    headers = {"Authorization": f"Bearer {_expired_jwt()}", "X-Workspace": "1"}

    for _ in range(30):
        response = await client.get("/api/0.6/user/details.json", headers=headers)
        assert response.status_code == 401

    assert len(_refusals(caplog)) == 1
