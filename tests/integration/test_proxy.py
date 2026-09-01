"""Integration tests for the OSM proxy (catch-all + capabilities) routes.

Covers the @test comments in api/main.py:
- STRIP_REQUEST_HEADERS are not forwarded upstream
- HOP_BY_HOP_HEADERS are not forwarded back to the client
- /api/capabilities.json is proxied without auth
- 4xx/5xx upstream responses are logged to Sentry and the status is preserved
- TENANT_BYPASSES allow specific paths/methods without an X-Workspace header
- only the decorator's methods are proxied (others -> 405)
- X-Workspace not in the user's accessible workspaces -> 403
- missing X-Workspace and no bypass -> 400
- Host / X-Real-IP / X-Forwarded-* are set correctly upstream
- the response is streamed back unmodified with its status and headers
"""

import httpx
import pytest

import api.main
from tests.support import factories, fakes
from tests.support.http import StreamingMockTransport


@pytest.fixture
def mock_osm(monkeypatch):
    """Default upstream returning 200 text/xml; records the forwarded request."""
    transport = StreamingMockTransport(
        lambda req: (200, {"content-type": "text/xml"}, b"<osm version='0.6'/>")
    )
    monkeypatch.setattr(
        api.main,
        "_osm_client",
        httpx.AsyncClient(transport=transport, base_url="http://osm-web"),
    )
    return transport


def install_osm(monkeypatch, handler):
    transport = StreamingMockTransport(handler)
    monkeypatch.setattr(
        api.main,
        "_osm_client",
        httpx.AsyncClient(transport=transport, base_url="http://osm-web"),
    )
    return transport


# --- capabilities ----------------------------------------------------------


async def test_capabilities_proxies_without_auth(client, mock_osm):
    response = await client.get("/api/capabilities.json")
    assert response.status_code == 200
    assert b"osm version" in response.content
    assert mock_osm.last_request.url.path == "/api/capabilities.json"


# --- auth / tenant gating --------------------------------------------------


async def test_missing_workspace_header_without_bypass_returns_400(
    client, login, mock_osm
):
    login(factories.make_user_info())
    response = await client.get("/api/0.6/map")
    assert response.status_code == 400


async def test_workspace_header_without_access_returns_403(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={}))
    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})
    assert response.status_code == 403


async def test_non_integer_workspace_header_returns_400(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get("/api/0.6/map", headers={"X-Workspace": "abc"})
    assert response.status_code == 400


async def test_contributor_request_is_proxied(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})
    assert response.status_code == 200
    assert b"osm version" in response.content


async def test_tenant_bypass_allows_workspace_put_without_header(
    client, login, mock_osm
):
    # PUT /api/0.6/workspaces/{id} is in TENANT_BYPASSES -> no X-Workspace needed.
    login(factories.make_user_info())
    response = await client.put("/api/0.6/workspaces/123")
    assert response.status_code == 200
    assert mock_osm.last_request is not None


async def test_tenant_bypass_does_not_apply_to_wrong_method(client, login, mock_osm):
    # The bypass for /workspaces/{id} is PUT/DELETE only; GET still needs a header.
    login(factories.make_user_info())
    response = await client.get("/api/0.6/workspaces/123")
    assert response.status_code == 400


async def test_disallowed_method_returns_405(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    # TRACE is not in the @app.api_route methods list.
    response = await client.request(
        "TRACE", "/api/0.6/map", headers={"X-Workspace": "1"}
    )
    assert response.status_code == 405


# --- header handling -------------------------------------------------------


async def test_spoofed_request_headers_are_stripped(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    await client.get(
        "/api/0.6/map",
        headers={
            "X-Workspace": "1",
            "Host": "evil.example",
            "X-Forwarded-For": "9.9.9.9",
            "X-Real-IP": "9.9.9.9",
        },
    )

    fwd = mock_osm.last_request.headers
    # Host is rewritten to the upstream host, not the spoofed value.
    assert fwd["host"] == "osm-web"
    # X-Forwarded-* / X-Real-IP are set by the proxy, not passed through.
    assert fwd["x-forwarded-for"] != "9.9.9.9"
    assert fwd["x-real-ip"] != "9.9.9.9"
    assert "x-forwarded-proto" in fwd
    assert "x-forwarded-host" in fwd


async def test_hop_by_hop_response_headers_are_stripped(client, login, monkeypatch):
    install_osm(
        monkeypatch,
        lambda req: (
            200,
            {"content-type": "text/xml", "keep-alive": "timeout=5", "x-custom": "v"},
            b"<osm/>",
        ),
    )
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})

    assert response.status_code == 200
    # Non-hop-by-hop headers pass through; hop-by-hop ones are dropped.
    assert response.headers.get("x-custom") == "v"
    assert "keep-alive" not in response.headers


# --- upstream status + body fidelity ---------------------------------------


async def test_upstream_error_is_logged_and_status_preserved(
    client, login, monkeypatch
):
    install_osm(monkeypatch, lambda req: (503, {"content-type": "text/plain"}, b"down"))
    captured = []
    monkeypatch.setattr(
        api.main.sentry_sdk, "capture_message", lambda msg: captured.append(msg)
    )
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})

    assert response.status_code == 503
    assert captured, "expected a Sentry capture_message for the 5xx upstream response"
    assert "503" in captured[0]


async def test_response_body_is_streamed_unmodified(client, login, monkeypatch):
    body = b"<osm>\n  <node id='1'/>\n</osm>"
    install_osm(
        monkeypatch, lambda req: (200, {"content-type": "application/xml"}, body)
    )
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))

    response = await client.get("/api/0.6/map", headers={"X-Workspace": "1"})

    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == "application/xml"


# --- /workspace/{id}/ path prefix ------------------------------------------
#
# Workspace selection for third-party clients that cannot set an X-Workspace
# header. The prefix must be stripped before proxying: osm-web (lighttpd)
# anchors its cgimap dispatch rules at ^/api/0\.6/, so a prefixed path would
# match none of them and be misrouted to osm-rails. See docs/deploy/lighttpd.conf.


async def test_path_prefix_selects_workspace_without_header(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get("/workspace/1/api/0.6/map")
    assert response.status_code == 200
    # Prefix stripped upstream, and the workspace re-emitted as a header.
    assert mock_osm.last_request is not None
    assert mock_osm.last_request.url.path == "/api/0.6/map"
    assert mock_osm.last_request.headers["X-Workspace"] == "1"


async def test_path_prefix_preserves_query_string(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get("/workspace/1/api/0.6/map?bbox=1,2,3,4")
    assert response.status_code == 200
    assert mock_osm.last_request is not None
    assert mock_osm.last_request.url.path == "/api/0.6/map"
    assert mock_osm.last_request.url.query == b"bbox=1,2,3,4"


async def test_path_prefix_without_access_returns_403(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={}))
    response = await client.get("/workspace/1/api/0.6/map")
    assert response.status_code == 403


async def test_path_prefix_matching_header_is_accepted(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get(
        "/workspace/1/api/0.6/map", headers={"X-Workspace": "1"}
    )
    assert response.status_code == 200
    assert mock_osm.last_request is not None
    # Exactly one X-Workspace upstream, not a duplicated pair.
    assert mock_osm.last_request.headers.get_list("X-Workspace") == ["1"]


async def test_path_prefix_conflicting_with_header_returns_400(client, login, mock_osm):
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1, 2]}))
    response = await client.get(
        "/workspace/1/api/0.6/map", headers={"X-Workspace": "2"}
    )
    assert response.status_code == 400
    assert "mismatch" in response.json()["detail"].lower()
    # Refused before proxying — nothing reached the upstream.
    assert mock_osm.last_request is None


async def test_client_supplied_workspace_header_is_replaced_upstream(
    client, login, mock_osm
):
    # The forwarded header comes from the resolved id, not the raw client copy.
    login(factories.make_user_info(accessible_workspace_ids={"pg": [7]}))
    response = await client.get("/api/0.6/map", headers={"X-Workspace": "007"})
    assert response.status_code == 200
    assert mock_osm.last_request is not None
    assert mock_osm.last_request.headers.get_list("X-Workspace") == ["7"]


async def test_no_workspace_header_sent_upstream_for_bypass_paths(
    client, login, mock_osm
):
    # TENANT_BYPASSES paths carry no workspace, so none should be invented.
    login(factories.make_user_info())
    response = await client.put("/api/0.6/workspaces/123")
    assert response.status_code == 200
    assert mock_osm.last_request is not None
    assert "X-Workspace" not in mock_osm.last_request.headers


async def test_bare_workspace_prefix_is_not_treated_as_selection(
    client, login, mock_osm
):
    # `/workspace/123` with no trailing path is not a prefix (matches nginx's
    # `^/workspace/(\d+)/`), so it still needs a header -> 400.
    login(factories.make_user_info(accessible_workspace_ids={"pg": [123]}))
    response = await client.get("/workspace/123")
    assert response.status_code == 400


async def test_path_prefix_is_stripped_before_api_v1_guard(client, login, mock_osm):
    # After stripping, this is an unrouted /api/v1 path -> clean 404, not a proxy.
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    response = await client.get("/workspace/1/api/v1/does-not-exist")
    assert response.status_code == 404
    assert mock_osm.last_request is None


async def test_path_prefix_is_stripped_before_changeset_create_detection(
    client, login, mock_osm, task_session
):
    """A prefixed changeset/create still gets the review_requested tag.

    Detection runs on the stripped path, so the prefix does not hide a
    changeset creation from the auto-flag logic.
    """
    login(factories.make_user_info(accessible_workspace_ids={"pg": [1]}))
    task_session.queue(fakes.rows(factories.make_workspace(id=1, autoFlagReview=True)))

    response = await client.put(
        "/workspace/1/api/0.6/changeset/create",
        content=b"<osm><changeset/></osm>",
        headers={"content-type": "text/xml"},
    )

    assert response.status_code == 200
    assert mock_osm.last_request is not None
    assert mock_osm.last_request.url.path == "/api/0.6/changeset/create"
    assert b'k="review_requested"' in mock_osm.last_request.content
