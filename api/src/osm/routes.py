from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.logging import get_logger
from api.core.security import UserInfo, validate_token
from api.src.osm.repository import OSMRepository
from api.src.osm.schemas import AugmentedDiffResponse
from api.src.workspaces.repository import WorkspaceRepository

logger = get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["osm"])


def get_osm_repo(
    session: AsyncSession = Depends(get_osm_session),
) -> OSMRepository:
    return OSMRepository(session)


def get_workspace_repo(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    return WorkspaceRepository(session)


@router.get(
    "/{workspace_id}/changesets/{changeset_id}/adiff",
    response_model=AugmentedDiffResponse,
)
async def get_changeset_adiff(
    workspace_id: int,
    changeset_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repo),
    repository_osm: OSMRepository = Depends(get_osm_repo),
    current_user: UserInfo = Depends(validate_token),
) -> AugmentedDiffResponse:
    try:
        await repository_ws.getById(current_user, workspace_id)
        rows = await repository_osm.getChangesetAdiff(workspace_id, changeset_id)

        return AugmentedDiffResponse.from_rows(rows)
    except Exception as e:
        logger.error(
            f"Failed to fetch adiff for changeset {changeset_id} "
            f"in workspace {workspace_id}: {str(e)}"
        )
        raise


@router.put(
    "/{workspace_id}/changesets/{changeset_id}/resolve",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def resolve_changeset(
    workspace_id: int,
    changeset_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repo),
    repository_osm: OSMRepository = Depends(get_osm_repo),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(
        workspace_id
    ) and not current_user.isWorkspaceValidator(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workspace leads and validators can resolve changesets",
        )

    await repository_ws.getById(current_user, workspace_id)

    try:
        await repository_osm.resolveChangeset(current_user, workspace_id, changeset_id)
    except Exception as e:
        logger.error(
            f"Failed to resolve changeset {changeset_id}"
            f" in workspace {workspace_id}: {str(e)}"
        )
        raise
