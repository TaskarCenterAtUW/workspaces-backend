import os
import re
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import NamedTuple
from xml.etree import ElementTree as ET

import httpx
import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.background import BackgroundTask

from api.core import config
from api.core.config import settings
from api.core.database import get_osm_session, get_task_session
from api.core.json_schema import close_json_schema_client, init_json_schema_client
from api.core.logging import get_logger, setup_logging
from api.core.security import (
    UserInfo,
    close_tdei_client,
    init_tdei_client,
    validate_token,
)
from api.src.osm.repository import OSMRepository
from api.src.osm.routes import router as osm_router
from api.src.tasking.audit.routes import router as tasking_audit_router
from api.src.tasking.projects.routes import me_router as tasking_me_router
from api.src.tasking.projects.routes import router as tasking_projects_router
from api.src.tasking.tasks.routes import router as tasking_tasks_router
from api.src.teams.routes import router as teams_router
from api.src.users.routes import router as users_router
from api.src.workspaces.jobs.routes import router as jobs_router
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.routes import router as workspaces_router
from api.utils.migrations import run_migrations

sentry_sdk.init(
    dsn=config.settings.SENTRY_DSN,
    environment=os.getenv("ENV", "unknown"),
    release=os.getenv("CODE_VERSION", "unknown"),
    debug=settings.DEBUG,
)

# Kept alongside `release` for any dashboards that query the `version` tag.
sentry_sdk.set_tag("version", os.getenv("CODE_VERSION", "unknown"))

# Set up logging configuration
setup_logging()

# Set up logger for this module
logger = get_logger(__name__)

# Shared HTTP client for OSM proxy. Reuses connection pool across requests:
_osm_client: httpx.AsyncClient | None = None


def _require_osm_client() -> httpx.AsyncClient:
    if _osm_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OSM proxy client is not initialized",
        )
    return _osm_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # only run migrations when not under test
    if "pytest" not in sys.modules:
        run_migrations()

    # Run before app bootstrap:
    global _osm_client
    _osm_client = httpx.AsyncClient(
        base_url=settings.WS_OSM_HOST,
        # 2 hour timeout for long-running OSM imports:
        timeout=httpx.Timeout(connect=10, read=7200, write=7200, pool=10),
    )
    init_tdei_client()
    init_json_schema_client()

    yield  # App runs

    # Run after app cleanup:
    await _osm_client.aclose()
    _osm_client = None
    await close_tdei_client()
    await close_json_schema_client()


app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
    swagger_ui_parameters={"syntaxHighlight": False},
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=100,
)

# Never log these header values verbatim (tokens/session identifiers):
_SENSITIVE_LOG_HEADERS = frozenset({"authorization", "cookie", "set-cookie"})


# Registered after CORSMiddleware, so it runs outermost and sees every
# request -- including OPTIONS preflights CORSMiddleware intercepts itself.
@app.middleware("http")
async def log_request_headers(request: Request, call_next):
    safe_headers = {
        k: ("<redacted>" if k.lower() in _SENSITIVE_LOG_HEADERS else v)
        for k, v in request.headers.items()
    }
    logger.info(f"{request.method} {request.url.path} headers={safe_headers}")
    return await call_next(request)


# Include routers
app.include_router(osm_router, prefix="/api/v1")
app.include_router(teams_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
app.include_router(tasking_projects_router, prefix="/api/v1")
app.include_router(tasking_me_router, prefix="/api/v1")
app.include_router(tasking_tasks_router, prefix="/api/v1")
app.include_router(tasking_audit_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Health check endpoint. Used for Docker."""
    return {"status": "ok"}


@app.get("/")
async def root():
    """Root endpoint. Redirects to the API documentation."""
    return RedirectResponse(url="/docs")


def get_workspace_repository(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    return WorkspaceRepository(session)


# @test: Any headers defined in STRIP_REQUEST_HEADERS are not forwarded to the OSM service
# @test: Any headers defined in HOP_BY_HOP_HEADERS are not forwarded to the client
# @test: /api/capabilities.json is proxied to the OSM service without requiring authentication
# @test: Every capabilities spelling (with and without a /workspace/{id}/ prefix) is served without authentication and forwards that same spelling upstream, prefix stripped
# @test: A capabilities path whose workspace id is not a non-negative integer returns 422 and is never proxied
# @test: The unauthenticated capabilities route forwards neither Authorization nor X-Workspace upstream, even when the client supplies them
# @test: Any request to the OSM service that returns a 4xx or 5xx status code is logged to Sentry with the correct message and the correct status code is returned to the client
# @test: Any request that matches a TENANTLESS_ENDPOINTS pattern/method proceeds without an X-Workspace header *only* if its authorize rule passes,
#       and any request that matches none of them and has no X-Workspace header returns a 400 Bad Request error
# @test: PUT/DELETE /api/0.6/workspaces/{id} requires isWorkspaceLead on that id, returning 403 otherwise (it creates/drops the OSM schema)
# @test: PUT /api/0.6/user/{auth_uid} is allowed only when auth_uid is the caller's own JWT sub, returning 403 otherwise
# @test: Only the methods defined in the @app.api_route decorator are allowed to be proxied to the OSM service, and any other methods return a 405 Method Not Allowed error
# @test: Any request with an X-Workspace header that does not match the user's accessible workspaces returns a 403 Forbidden error
# @test: Any request with a missing X-Workspace header that matches no TENANTLESS_ENDPOINTS entry returns a 400 Bad Request error
# @test: An empty or whitespace-only X-Workspace header is a malformed value and returns 400, not 403
# @test: The Authorization header sent upstream is always `Bearer <token>`, including when the caller authenticated with HTTP Basic, and replaces any client-supplied copy
# @test: A caller that accepts deflate has exactly one Accept-Encoding upstream, set to "deflate", and the response's Content-Encoding reaches the client unchanged
# @test: A caller that cannot accept deflate has its own Accept-Encoding forwarded, unmodified and un-duplicated
# @test: A `/workspace/{id}/...` path prefix selects the workspace without an X-Workspace header, is authorized the same way, and is stripped from the path proxied upstream
# @test: A `/workspace/{id}/...` prefix whose id disagrees with an X-Workspace header returns a 400 Bad Request error
# @test: A `/workspace/{id}/...` prefix on a workspace the user cannot access returns a 403 Forbidden error
# @test: A workspace id in the HTTP Basic username selects the workspace with no path prefix and no X-Workspace header, is authorized the same way, and is not forwarded upstream as a credential
# @test: A workspace id in the HTTP Basic username that disagrees with a path prefix or an X-Workspace header returns a 400 Bad Request error
# @test: A workspace id in the HTTP Basic username that agrees with a path prefix or an X-Workspace header is accepted
# @test: A workspace id in the HTTP Basic username on a workspace the user cannot access returns a 403 Forbidden error
# @test: The X-Workspace header sent upstream carries the resolved workspace id, replacing any client-supplied copy, and is absent when no workspace applies
# @test: A `/workspace/{id}/...` prefix is stripped before the TENANTLESS_ENDPOINTS match and changeset-create detection, so both match the underlying path
# @test: All the values for Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Host, and X-Forwarded-Proto headers are correctly set when proxied to the OSM service
# @test: The response from the OSM service is correctly streamed back to the client with the correct status code and headers, and the response body is not modified in any way

# This API route catches anything not otherwise defined above--MUST be last in this file
#
# h/t: https://stackoverflow.com/questions/70610266/proxy-an-external-website-using-python-fast-api-not-supporting-query-params
#

# According to HTTP/1.1, a proxy must not forward these "hop-by-hop" headers.
# We also exclude Content-Length because httpx recomputes the length from the
# actual content bytes, and this value may differ from the original after the
# proxy rewrites body content (i.e. after changset tag injection).
HOP_BY_HOP_HEADERS = frozenset(
    [
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)

# Asked of the OSM gateway in place of the caller's own Accept-Encoding, where
# the caller can accept it.
#
# This is a mitigation, not a fix, for AB#4307. The FastCGI connection between
# the gateway (lighttpd) and osm-cgimap loses data in flight: cgimap logs the
# request "Completed ... returning 25127467 bytes" while lighttpd, 6ms later,
# logs "unexpected end-of-file (perhaps the fastcgi process died)". Neither
# process fails, so the client receives HTTP 200, a cleanly terminated chunked
# stream, and a truncated document. Consumers then report corrupt OSM XML from
# somewhere well downstream.
#
# The loss scales with how many bytes are outstanding when the connection
# closes. On a 25MB changeset download, measured against the gateway directly:
#
#     identity (25.1MB on the wire)   6/6 truncated
#     br       (~1.2MB)               3/10 truncated
#     deflate  (1.4MB)                0/18 truncated
#
# Asking for deflate makes the window small enough to miss. It does not close
# it, and it does nothing for a caller that cannot accept deflate -- which is
# why this is scoped rather than unconditional. Remove it once the gateway is
# fixed; a comment in AB#4307 has the full diagnosis.
_UPSTREAM_ACCEPT_ENCODING = b"deflate"


# @test: A caller whose Accept-Encoding allows deflate has it replaced with exactly "deflate" upstream
# @test: A caller that cannot accept deflate (identity, absent, or deflate;q=0) has its Accept-Encoding forwarded untouched
def _accepts_deflate(accept_encoding: str | None) -> bool:
    """Whether a caller's Accept-Encoding permits a deflate-coded response.

    An absent header means "any coding is acceptable" under RFC 9110, but in
    practice a caller that sends none is usually one that does not decode
    content codings at all. Treated as a no, so this never hands anyone a body
    it cannot read.
    """
    if not accept_encoding:
        return False

    for element in accept_encoding.split(","):
        token, _, params = element.strip().partition(";")

        if token.strip().lower() not in ("deflate", "*"):
            continue

        # "deflate;q=0" is the one way a caller can name a coding while refusing
        # it, so a match is not enough on its own.
        quality = 1.0

        for param in params.split(";"):
            name, _, value = param.partition("=")

            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0

        if quality > 0:
            return True

    return False


# Do not forward spoofed reverse-proxy informational headers:
STRIP_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | {
    "host",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
}

# osm-rails sets its own Access-Control-*/Vary headers on some API responses
# (e.g. GET /api/0.6/users) for direct browser access. Forwarding those
# verbatim alongside our own CORSMiddleware's headers produces duplicate,
# conflicting values that browsers reject as a CORS error.
STRIP_RESPONSE_HEADERS = HOP_BY_HOP_HEADERS | {
    "vary",
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "access-control-max-age",
}


class TenantlessEndpoint(NamedTuple):
    """A proxied path that carries no tenant schema, plus its own auth rule."""

    pattern: re.Pattern[str]
    methods: set[str]
    # (caller, path match) -> may this caller perform this operation?
    authorize: Callable[["UserInfo", "re.Match[str]"], bool]
    denial: str


def _may_manage_workspace_schema(user: "UserInfo", match: "re.Match[str]") -> bool:
    """Creating or dropping a workspace's OSM schema is a lead-level action.

    Mirrors the native `DELETE /api/v1/workspaces/{id}` gate. Safe for create:
    `create_workspace` assigns the creator LEAD and evicts their cache before
    returning the id the client then PUTs here, so the role is in place.
    """
    return user.isWorkspaceLead(int(match.group(1)))


def _is_provisioning_self(user: "UserInfo", match: "re.Match[str]") -> bool:
    """A caller may provision only their own OSM user row.

    The OSM `auth_uid` *is* the JWT `sub`, and the frontend only ever calls this
    with its own subject. Without the check, any authenticated user could
    overwrite another user's email and display_name, since rails
    `UsersController` sets `skip_authorization_check :only => [:provision]`.
    """
    return match.group(1) == str(user.user_uuid)


# Creating (PUT) or dropping (DELETE) a workspace's OSM schema.
_WORKSPACE_SCHEMA_RE = re.compile(r"^/api/0\.6/workspaces/(\d+)$")


# Paths that cannot require an `X-Workspace` header, because no tenant schema
# applies: cgimap/rails resolve the header to `SET search_path TO
# "workspace-<id>"`, which has nothing to point at while a schema is being
# created or dropped, or when the row is global (`users` lives in `public`).
#
# They are NOT unauthorized. The resource id is in the path, so each entry
# carries its own rule. Downstream cannot be relied on for this: rails'
# `WorkspacesController` has `before_action :authorize` commented out plus
# `skip_authorization_check` (with a TODO pointing back at this service), and
# `UsersController#provision` skips it too.
TENANTLESS_ENDPOINTS: list[TenantlessEndpoint] = [
    # Creating/dropping a workspace's schema (`Apartment::Tenant.create/drop`).
    TenantlessEndpoint(
        _WORKSPACE_SCHEMA_RE,
        {"PUT", "DELETE"},
        _may_manage_workspace_schema,
        "You must be a workspace lead to create or delete this workspace",
    ),
    # Provisioning the caller's own user row during authentication.
    TenantlessEndpoint(
        re.compile(r"^/api/0\.6/user/([^/]+)$"),
        {"PUT"},
        _is_provisioning_self,
        "You may only provision your own user",
    ),
]

# Changeset create path — buffered for potential tag injection
_CHANGESET_CREATE_RE = re.compile(r"^/api/0\.6/changeset/create$")

# Workspace selection via a URL path prefix (`/workspace/{id}/...`), for
# third-party clients that cannot set an `X-Workspace` header. Mirrors the
# nginx `location ~ ^/workspace/(\d+)/` rule, which is not deployed --
# `osm-web` runs lighttpd, which has no prefix handling (see
# `docs/deploy/lighttpd.conf`). The prefix MUST be stripped before proxying:
# lighttpd anchors its cgimap dispatch rules at `^/api/0\.6/`, so a prefixed
# path would match none of them and be misrouted to osm-rails as a 404.
# Requires a trailing path segment, like the nginx rule it replaces, so a bare
# `/workspace/123` is left alone.
_WORKSPACE_PREFIX_RE = re.compile(r"^/workspace/(\d+)(/.*)$")


# OSM clients ask for capabilities before anything else, and do so anonymously -- JOSM will not send
# credentials until it has been challenged, and it cannot act on the Bearer challenge the rest of this
# surface answers with. Serve every spelling they use, with or without the /workspace/{id}/ prefix.
# These are declared above the catch-all so they match first and never reach validate_token.
@app.get("/api/capabilities")
@app.get("/api/capabilities.json")
@app.get("/api/0.6/capabilities")
@app.get("/workspace/{workspace_id}/api/capabilities")
@app.get("/workspace/{workspace_id}/api/capabilities.json")
@app.get("/workspace/{workspace_id}/api/0.6/capabilities")
async def capabilities(request: Request, workspace_id: int | None = None):
    """Proxy the OSM capabilities manifest without requiring authentication.

    The manifest is public metadata and carries nothing workspace-specific, so the
    `/workspace/{id}/` prefix is accepted only because clients are configured with it as their
    server URL; it is stripped and otherwise ignored.
    """

    client = _require_osm_client()
    client_host = request.client.host if request.client else "unknown"
    req_headers = [
        (k.encode(), v.encode())
        for k, v in request.headers.items()
        # This route is deliberately unauthenticated, so it validates neither a
        # token nor a workspace. Drop both rather than relaying them upstream
        # unchecked: the manifest is global public metadata, so neither changes
        # the response, and `catch_all` only ever forwards values it authorized.
        if k.lower() not in STRIP_REQUEST_HEADERS
        and k.lower() not in ("authorization", "x-workspace")
    ] + [
        (b"Host", client.base_url.host.encode()),
        (b"X-Real-IP", client_host.encode()),
        (b"X-Forwarded-For", client_host.encode()),
        (b"X-Forwarded-Host", (request.url.hostname or "").encode()),
        (b"X-Forwarded-Proto", request.url.scheme.encode()),
    ]

    # Forward whichever spelling was asked for, minus any workspace prefix, rather than a fixed path.
    proxied_path = request.url.path
    if proxied_path.startswith("/workspace/"):
        # The route's `int` annotation accepts negatives, which `_WORKSPACE_PREFIX_RE`
        # (`\d+`) does not. Without this, a path like /workspace/-1/api/capabilities
        # went upstream with the prefix intact and came back 500 -- on an endpoint
        # anyone can call. Refuse it the way FastAPI refuses a non-integer id.
        prefix_match = _WORKSPACE_PREFIX_RE.match(proxied_path)
        if prefix_match is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Workspace id in the path must be a non-negative integer",
            )
        proxied_path = prefix_match.group(2)

    url = httpx.URL(path=proxied_path)
    rp_req = client.build_request("GET", url, headers=req_headers)

    try:
        rp_resp = await client.send(rp_req, stream=True)
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Upstream OSM service timed out",
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not connect to upstream OSM service",
        )

    forwarded_headers = {
        k: v
        for k, v in rp_resp.headers.items()
        if k.lower() not in STRIP_RESPONSE_HEADERS
    }

    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=forwarded_headers,
        background=BackgroundTask(rp_resp.aclose),
    )


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def catch_all(
    request: Request,
    current_user: UserInfo = Depends(validate_token),
    repository: WorkspaceRepository = Depends(get_workspace_repository),
    osm_session: AsyncSession = Depends(get_osm_session),
):
    """
    Catch-all route to proxy requests to the OSM service.
    """

    # Resolve the `/workspace/{id}/...` prefix first, so everything below --
    # the /api/v1/ guard, TENANTLESS_ENDPOINTS, changeset-create detection, and the
    # URL actually proxied -- all operate on the normalized path.
    prefix_workspace_id: int | None = None
    proxied_path = request.url.path
    prefix_match = _WORKSPACE_PREFIX_RE.match(proxied_path)
    if prefix_match is not None:
        prefix_workspace_id = int(prefix_match.group(1))
        proxied_path = prefix_match.group(2)

    # `/api/v1/...` paths belong to the FastAPI routers (workspaces,
    # users, teams, tasking-projects, tasking-tasks). If none matched,
    # the URL is wrong or the method is unsupported — surface that as
    # a clean 404 instead of letting the OSM proxy swallow it with a
    # misleading "No X-Workspace header supplied".
    if proxied_path.startswith("/api/v1/"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No handler for {request.method} {request.url.path}. "
                "Check the method and path; this URL is not proxied to OSM."
            ),
        )

    workspace_id: int | None = None
    header_workspace = request.headers.get("X-Workspace")
    if header_workspace is not None:
        try:
            # Parsed directly: an empty value is a malformed header, so it
            # raises here for the documented 400. (It used to fall back to
            # "-1", which no user can access, surfacing as a misleading 403.)
            workspace_id = int(header_workspace)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Workspace header must be a valid integer",
            )

        # A path prefix and a header that disagree is a client bug. Refuse
        # rather than silently authorizing one workspace while the caller
        # believes it is addressing the other.
        if prefix_workspace_id is not None and prefix_workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Workspace mismatch: path prefix says {prefix_workspace_id}, "
                    f"X-Workspace header says {workspace_id}"
                ),
            )
    elif prefix_workspace_id is not None:
        workspace_id = prefix_workspace_id

    # Third source: the workspace id in the HTTP Basic username, set by
    # `TDEIHTTPBearer` when the caller put its token in the password instead.
    # It exists for editors that cannot carry a 1.4KB token in a username
    # field, which are the same editors that cannot be pointed at a URL with a
    # workspace-specific prefix -- so this is usually the *only* source.
    basic_workspace_id = getattr(request.state, "basic_workspace_id", None)
    if basic_workspace_id is not None:
        # Same reasoning as the prefix/header check above: two sources that
        # disagree is a client bug, not something to resolve by precedence.
        if workspace_id is not None and workspace_id != basic_workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Workspace mismatch: the request path or X-Workspace header "
                    f"says {workspace_id}, the HTTP Basic username says "
                    f"{basic_workspace_id}"
                ),
            )
        workspace_id = basic_workspace_id

    if workspace_id is not None:
        if not current_user.isWorkspaceContributor(workspace_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this workspace",
            )
    else:
        # No tenant header. The path may still be a tenantless endpoint -- but
        # those authorize against the id in the path rather than skipping the
        # check entirely.
        matched: tuple[TenantlessEndpoint, re.Match[str]] | None = None
        for endpoint in TENANTLESS_ENDPOINTS:
            if request.method not in endpoint.methods:
                continue
            path_match = endpoint.pattern.fullmatch(proxied_path)
            if path_match is not None:
                matched = (endpoint, path_match)
                break

        if matched is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No X-Workspace header supplied",
            )

        endpoint, path_match = matched
        if not endpoint.authorize(current_user, path_match):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=endpoint.denial,
            )

    url = httpx.URL(path=proxied_path.strip(), query=request.url.query.encode("utf-8"))

    client = _require_osm_client()
    client_host = request.client.host if request.client else "unknown"
    # See _UPSTREAM_ACCEPT_ENCODING: ask the gateway for deflate where the caller
    # can read it, because the uncompressed response is the one that arrives
    # truncated.
    force_deflate = _accepts_deflate(request.headers.get("Accept-Encoding"))

    # All re-emitted below from the values this route resolved, so any client
    # copy is dropped: the proxied request must carry exactly one of each, and
    # lighttpd/cgimap and osm-rails must see the same workspace and token this
    # route authorized.
    replaced_headers = ("x-workspace", "authorization") + (
        ("accept-encoding",) if force_deflate else ()
    )

    req_headers = [
        (k.encode(), v.encode())
        for k, v in request.headers.items()
        if k.lower() not in STRIP_REQUEST_HEADERS and k.lower() not in replaced_headers
    ] + [
        (b"Host", client.base_url.host.encode()),
        (b"X-Real-IP", client_host.encode()),
        (b"X-Forwarded-For", client_host.encode()),
        (b"X-Forwarded-Host", (request.url.hostname or "").encode()),
        (b"X-Forwarded-Proto", request.url.scheme.encode()),
    ]
    if workspace_id is not None:
        req_headers.append((b"X-Workspace", str(workspace_id).encode()))
    # Always Bearer upstream: osm-rails/doorkeeper has no Basic path, so a
    # caller's Basic credentials (accepted by TDEIHTTPBearer) must be
    # normalized here or the token bridge's work is wasted. `current_user`
    # already carries the validated token, so this needs no second auth
    # dependency. For a caller who already sent Bearer it reproduces the
    # original header byte for byte.
    req_headers.append(
        (b"Authorization", f"Bearer {current_user.credentials}".encode())
    )

    if force_deflate:
        req_headers.append((b"Accept-Encoding", _UPSTREAM_ACCEPT_ENCODING))

    # For changeset creation, inject review_requested tag for contributors:
    request_content: object = request.stream()
    if (
        workspace_id is not None
        and request.method == "PUT"
        and _CHANGESET_CREATE_RE.fullmatch(proxied_path)
    ):
        workspace = await repository.getById(current_user, workspace_id)

        if (
            workspace.autoFlagReview
            and current_user.effective_role(workspace_id) == "contributor"
        ):
            logger.info("Injecting review request tag")
            body = await request.body()
            root = ET.fromstring(body)
            changeset_el = root.find("changeset")
            if changeset_el is not None:
                ET.SubElement(changeset_el, "tag", k="review_requested", v="yes")
            request_content = ET.tostring(root, encoding="unicode").encode("utf-8")
        else:
            # Body was not consumed; fall back to buffered bytes to avoid
            # double-read issues after the workspace fetch above.
            request_content = await request.body()

    if request.method == "GET":
        # No content to send for GET requests
        rp_req = client.build_request(request.method, url, headers=req_headers)
    else:
        rp_req = client.build_request(
            request.method, url, headers=req_headers, content=request_content
        )

    try:
        rp_resp = await client.send(rp_req, stream=True)
        logger.info(f"Upstream request to {rp_req.url} sent successfully")
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Upstream OSM service timed out",
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not connect to upstream OSM service",
        )

    if rp_resp.status_code >= 400 and rp_resp.status_code < 600:
        msg = (
            f"Upstream request to {rp_req.url} returned "
            f"status code {rp_resp.status_code}"
        )
        sentry_sdk.capture_message(msg)
        logger.warning(msg)

    # Rails creates the workspace schema by cloning `public`, which includes
    # this service's tables. Drop those copies, as Rails does for `users`, so
    # nothing can resolve to them in place of `public`. A failure is reported
    # rather than failing the create: the schema exists either way, and the
    # copies only matter to a search_path that puts the workspace first.
    if (
        request.method == "PUT"
        and rp_resp.status_code < 300
        and (create_match := _WORKSPACE_SCHEMA_RE.fullmatch(proxied_path))
    ):
        try:
            await OSMRepository(osm_session).dropServiceTableCopies(
                int(create_match.group(1))
            )
        except Exception as e:  # noqa: BLE001
            sentry_sdk.capture_exception(e)
            logger.error(f"Could not drop table copies from new workspace: {e}")

    forwarded_headers = {
        k: v
        for k, v in rp_resp.headers.items()
        if k.lower() not in STRIP_RESPONSE_HEADERS
    }

    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=forwarded_headers,
        background=BackgroundTask(rp_resp.aclose),
    )
