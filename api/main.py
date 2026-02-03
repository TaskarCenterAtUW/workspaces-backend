import os
import re
from contextlib import asynccontextmanager

import httpx
import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.background import BackgroundTask

from api.core import config
from api.core.config import settings
from api.core.database import get_task_session
from api.core.logging import get_logger, setup_logging
from api.core.security import UserInfo, validate_token
from api.src.teams.routes import router as teams_router
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.routes import router as workspaces_router
from api.utils.migrations import run_migrations

sentry_sdk.init(
    dsn=config.settings.SENTRY_DSN,
    environment=os.getenv("ENV", "unknown"),
    debug=settings.DEBUG,
)

sentry_sdk.set_tag("version", os.getenv("CODE_VERSION", "unknown"))

# Set up logging configuration
setup_logging()

# Optional: Run migrations on startup
run_migrations()

# Set up logger for this module
logger = get_logger(__name__)

# Shared HTTP client for OSM proxy. Reuses connection pool across requests:
_osm_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Run before app bootstrap:
    global _osm_client
    _osm_client = httpx.AsyncClient(
        base_url=settings.WS_OSM_HOST,
        # 2 hour timeout for long-running OSM imports:
        timeout=httpx.Timeout(connect=10, read=7200, write=7200, pool=10),
    )

    yield  # App runs

    # Run after app cleanup:
    await _osm_client.aclose()
    _osm_client = None


app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
    swagger_ui_parameters={"syntaxHighlight": False},
    lifespan=lifespan,
)

# Include routers
app.include_router(teams_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")

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


# This API route catches anything not otherwise defined above--MUST be last in this file
#
# h/t: https://stackoverflow.com/questions/70610266/proxy-an-external-website-using-python-fast-api-not-supporting-query-params
#

# According to HTTP/1.1, a proxy must not forward these "hop-by-hop" headers:
HOP_BY_HOP_HEADERS = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)

# Define paths that do not require X-Workspace header
AUTH_WHITELIST_PATTERNS = [
    re.compile(p)
    for p in [
        r"^/api/0\.6/user/.*$",  # used during authentication
        r"^/api/0\.6/workspaces/[0-9]+/bbox\.json$",  # used to get workspace bbox without workspace header, to be removed
    ]
]


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
    authorizedWorkspace = None

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

        authorizedWorkspace = workspace_id
    else:
        if not any(p.fullmatch(request.url.path) for p in AUTH_WHITELIST_PATTERNS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No X-Workspace header supplied",
            )

    url = httpx.URL(
        path=request.url.path.strip(), query=request.url.query.encode("utf-8")
    )

    client = _osm_client
    new_headers = list()
    new_headers.append(
        (bytes("Authorization", "utf-8"), request.headers.get("Authorization"))
    )

    if authorizedWorkspace is not None:
        new_headers.append(
            (
                bytes("X-Workspace", "utf-8"),
                bytes(str(authorizedWorkspace), "utf-8"),
            )
        )

    new_headers.append((bytes("Host", "utf-8"), bytes(client.base_url.host, "utf-8")))
    rp_req = client.build_request(
        request.method, url, headers=new_headers, content=request.stream()
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
