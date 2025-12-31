import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from api.core.config import settings
from api.core.database import get_session
from api.core.logging import get_logger, setup_logging
from api.core.security import UserInfo, validate_token
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.routes import router as workspaces_router
from api.src.workspaces.service import WorkspaceService
from api.utils.migrations import run_migrations

# Set up logging configuration
setup_logging()

# Optional: Run migrations on startup
run_migrations()

# Set up logger for this module
logger = get_logger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    debug=settings.DEBUG,
)

# Include routers
app.include_router(workspaces_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/")
async def root():
    """Root endpoint."""
    logger.debug("Root endpoint called")
    return {"message": "Welcome to Workspaces API!"}


def get_workspace_service(
    session: AsyncSession = Depends(get_session),
) -> WorkspaceService:
    repository = WorkspaceRepository(session)
    return WorkspaceService(repository)


# This API route catches anything not otherwise defined above--MUST be last in this file
#
# h/t: https://stackoverflow.com/questions/70610266/proxy-an-external-website-using-python-fast-api-not-supporting-query-params
#
@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE"],
)
async def catch_all(
    request: Request,
    current_user: UserInfo = Depends(validate_token),
    service: WorkspaceService = Depends(get_workspace_service),
):
    authorizedWorkspace = None

    if(request.headers.get("X-Workspace") is not None):
        authorizedWorkspace = await service.get_workspace(
            current_user, int(request.headers.get("X-Workspace") or "-1")
        )

        # user specified a workspace they wanted access to, but didn't get it
        if authorizedWorkspace is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    print(
        f"Path proxied: {request.url} workspace_id: {request.headers.get("X-Workspace")}"
    )

    url = httpx.URL(
        path=request.url.path.strip(), query=request.url.query.encode("utf-8")
    )
    client = httpx.AsyncClient(base_url=settings.WS_OSM_HOST)

    new_headers = list()
    new_headers.append(
        (bytes("Authorization", "utf-8"), request.headers.get("Authorization"))
    )

    if(authorizedWorkspace is not None):
        new_headers.append(
            (bytes("X-Workspace", "utf-8"), bytes(str(authorizedWorkspace.id), "utf-8"))
        )
    new_headers.append((bytes("Host", "utf-8"), bytes(client.base_url.host, "utf-8")))

    rp_req = client.build_request(
        request.method, url, headers=new_headers, content=await request.body()
    )
    rp_resp = await client.send(rp_req, stream=True)
    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=rp_resp.headers,
        background=BackgroundTask(rp_resp.aclose),
    )
