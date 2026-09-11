from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session
from api.core.security import UserInfo, validate_token
from api.src.workspaces.jobs.repository import JobRepository
from api.src.workspaces.jobs.schemas import Job

router = APIRouter(prefix="/workspaces/jobs", tags=["jobs"])


def get_job_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> JobRepository:
    return JobRepository(session)


@router.get("/{job_id}", response_model=Job)
async def get_job_by_id(
    job_id: int,
    repository: JobRepository = Depends(get_job_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Job:
    return await repository.getById(current_user, job_id)
