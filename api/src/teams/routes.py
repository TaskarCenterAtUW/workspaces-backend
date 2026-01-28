from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token
from api.src.workspaces.repository import OSMRepository, WorkspaceRepository
from api.src.workspaces.service import OSMService, WorkspaceService

# FIXME: make these consistent with response codes etc?

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


def get_workspace_service(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceService:
    repository = WorkspaceRepository(session)
    return WorkspaceService(repository)

def get_osm_service(
    session: AsyncSession = Depends(get_osm_session),
) -> OSMService:
    repository = OSMRepository(session)
    return OSMService(repository)


# Returns JSON payload or 204 if not found
@router.get("/{team_id}")
async def get_team(
    team_id: int,
    current_user: UserInfo = Depends(validate_token),
):
    pass

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_team(
    current_user: UserInfo = Depends(validate_token),
):
    pass

@router.post("/{team_id}", status_code=status.HTTP_201_CREATED)
async def add_user_to_team(
    current_user: UserInfo = Depends(validate_token),
):
    pass

@router.patch("/{team_id}", status_code=status.HTTP_201_CREATED)
async def update_team(
    current_user: UserInfo = Depends(validate_token),
):
    pass

@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    current_user: UserInfo = Depends(validate_token),
):
    pass

@router.delete("/{team_id}/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_from_team(
    current_user: UserInfo = Depends(validate_token),
):
    pass