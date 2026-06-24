"""Integration tests for the OSM proxy (catch-all) routes.

The proxy forwards to an upstream OSM service via a module-level
``httpx.AsyncClient`` and re-streams the response. We swap that client for
one backed by a streamable mock transport so the proxy path is exercised
end-to-end without a real upstream.
"""

import pytest

import api.main
from tests.support import factories
from tests.support.http import osm_mock_client


@pytest.fixture
def mock_osm(monkeypatch):
    """Replace the upstream OSM client with a recording mock transport."""
    client, transport = osm_mock_client()
    monkeypatch.setattr(api.main, "_osm_client", client)
    return transport


async def test_capabilities_proxies_without_auth(client, mock_osm):
    # No X-Workspace header and no bearer token required for capabilities.
    response = await client.get("/api/capabilities.json")

    assert response.status_code == 200
    assert b"osm version" in response.content
    assert mock_osm.last_request.url.path == "/api/capabilities.json"


async def test_proxy_requires_workspace_header(client, login, mock_osm):
    login(factories.make_user_info())
    # A non-whitelisted path with no X-Workspace header is rejected.
    response = await client.get("/api/0.6/map")

    assert response.status_code == 400


async def test_proxy_forbidden_without_workspace_access(client, login, mock_osm):
    # User has no access to workspace 1.
    login(factories.make_user_info(accessible_workspace_ids={}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})

    assert response.status_code == 403


async def test_proxy_forwards_for_workspace_contributor(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})

    assert response.status_code == 200
    assert b"osm version" in response.content
    # Spoofable forwarding headers are normalized before reaching upstream.
    forwarded = mock_osm.last_request.headers
    assert "x-forwarded-for" in forwarded  # set by the proxy itself
    assert forwarded["host"] == "osm-web"


async def test_proxy_rejects_non_integer_workspace_header(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "abc"})

    assert response.status_code == 400
