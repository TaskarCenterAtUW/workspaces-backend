import os
import re
import sys
from contextlib import asynccontextmanager
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
from api.core.database import get_task_session
from api.core.json_schema import close_json_schema_client, init_json_schema_client
from api.core.logging import get_logger, setup_logging
from api.core.security import (
    UserInfo,
    close_tdei_client,
    init_tdei_client,
    validate_token,
)
from api.src.osm.routes import router as osm_router
from api.src.tasking.audit.routes import router as tasking_audit_router
from api.src.tasking.projects.routes import me_router as tasking_me_router
from api.src.tasking.projects.routes import router as tasking_projects_router
from api.src.tasking.tasks.routes import router as tasking_tasks_router
from api.src.teams.routes import router as teams_router
from api.src.users.routes import router as users_router
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

# Include routers
app.include_router(osm_router, prefix="/api/v1")
app.include_router(teams_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
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
# @test: Any request to the OSM service that returns a 4xx or 5xx status code is logged to Sentry with the correct message and the correct status code is returned to the client
# @test: Any request that matches the RegEx in TENANT_BYPASSES is allowed to proceed without an X-Workspace header,
#       and any request that does not match the Regex in TENANT_BYPASSES and does not have an X-Workspace header returns a 400 Bad Request error
# @test: Only the methods defined in the @app.api_route decorator are allowed to be proxied to the OSM service, and any other methods return a 405 Method Not Allowed error
# @test: Any request with an X-Workspace header that does not match the user's accessible workspaces returns a 403 Forbidden error
# @test: Any request with a missing X-Workspace header that does not match the TENANT_BYPASSES returns a 400 Bad Request error
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

# Do not forward spoofed reverse-proxy informational headers:
STRIP_REQUEST_HEADERS = HOP_BY_HOP_HEADERS | {
    "host",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
}

# Paths that do not require X-Workspace header, scoped by HTTP method. Each
# entry is a tuple of: (compiled regex, set of allowed methods).
TENANT_BYPASSES: list[tuple[re.Pattern[str], set[str]]] = [
    # Creating/deleting a workspace (no tenant context applies):
    (re.compile(r"^/api/0\.6/workspaces/\d+$"), {"PUT", "DELETE"}),
    # Provisioning users during authentication:
    (re.compile(r"^/api/0\.6/user/[^/]+$"), {"PUT"}),
]

# Changeset create path — buffered for potential tag injection
_CHANGESET_CREATE_RE = re.compile(r"^/api/0\.6/changeset/create$")


@app.get("/api/capabilities.json")
async def capabilities(request: Request):
    """Proxy OSM capabilities manifest without requiring authentication."""

    client = _require_osm_client()
    client_host = request.client.host if request.client else "unknown"
    req_headers = [
        (k.encode(), v.encode())
        for k, v in request.headers.items()
        if k.lower() not in STRIP_REQUEST_HEADERS
    ] + [
        (b"Host", client.base_url.host.encode()),
        (b"X-Real-IP", client_host.encode()),
        (b"X-Forwarded-For", client_host.encode()),
        (b"X-Forwarded-Host", (request.url.hostname or "").encode()),
        (b"X-Forwarded-Proto", request.url.scheme.encode()),
    ]

    url = httpx.URL(path="/api/capabilities.json")
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
        k: v for k, v in rp_resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
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
):
    """
    Catch-all route to proxy requests to the OSM service.
    """

    # `/api/v1/...` paths belong to the FastAPI routers (workspaces,
    # users, teams, tasking-projects, tasking-tasks). If none matched,
    # the URL is wrong or the method is unsupported — surface that as
    # a clean 404 instead of letting the OSM proxy swallow it with a
    # misleading "No X-Workspace header supplied".
    if request.url.path.startswith("/api/v1/"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No handler for {request.method} {request.url.path}. "
                "Check the method and path; this URL is not proxied to OSM."
            ),
        )

    workspace_id: int | None = None
    if request.headers.get("X-Workspace") is not None:
        try:
            workspace_id = int(request.headers.get("X-Workspace") or "-1")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Workspace header must be a valid integer",
            )

        if not current_user.isWorkspaceContributor(workspace_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this workspace",
            )
    else:
        if not any(
            p.fullmatch(request.url.path) and request.method in methods
            for p, methods in TENANT_BYPASSES
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No X-Workspace header supplied",
            )

    url = httpx.URL(
        path=request.url.path.strip(), query=request.url.query.encode("utf-8")
    )

    client = _require_osm_client()
    client_host = request.client.host if request.client else "unknown"
    req_headers = [
        (k.encode(), v.encode())
        for k, v in request.headers.items()
        if k.lower() not in STRIP_REQUEST_HEADERS
    ] + [
        (b"Host", client.base_url.host.encode()),
        (b"X-Real-IP", client_host.encode()),
        (b"X-Forwarded-For", client_host.encode()),
        (b"X-Forwarded-Host", (request.url.hostname or "").encode()),
        (b"X-Forwarded-Proto", request.url.scheme.encode()),
    ]

    # For changeset creation, inject review_requested tag for contributors:
    request_content: object = request.stream()
    if (
        workspace_id is not None
        and request.method == "PUT"
        and _CHANGESET_CREATE_RE.fullmatch(request.url.path)
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

    rp_req = client.build_request(
        request.method, url, headers=req_headers, content=request_content
    )
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

    if rp_resp.status_code >= 400 and rp_resp.status_code < 600:
        msg = (
            f"Upstream request to {rp_req.url} returned "
            f"status code {rp_resp.status_code}"
        )
        sentry_sdk.capture_message(msg)
        logger.warning(msg)

    forwarded_headers = {
        k: v for k, v in rp_resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }

    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=forwarded_headers,
        background=BackgroundTask(rp_resp.aclose),
    )
