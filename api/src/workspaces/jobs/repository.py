from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import (
    AlreadyExistsException,
    ForbiddenException,
    NotFoundException,
)
from api.core.security import UserInfo
from api.src.workspaces.jobs.schemas import Job, JobCreate, JobPatch


class JobRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _accessible_workspace_ids(current_user: UserInfo) -> list[int]:
        workspace_ids: list[int] = []
        for ids in current_user.accessibleWorkspaceIds.values():
            workspace_ids.extend(ids)
        return workspace_ids

    async def create(self, current_user: UserInfo, job_data: JobCreate) -> Job:
        # if not current_user.isWorkspaceContributor(job_data.workspace_id):
        #     raise ForbiddenException(
        #         "User does not have permissions to create a job in that workspace."
        #     )
        # Not needed as this may be during the initial creation. There is no API call to create a job.
        # The job is created internally when a workspace is created. So, we don't need to check for permissions here.

        job = Job(**job_data.model_dump())

        try:
            self.session.add(job)
            await self.session.commit()
            await self.session.refresh(job)
            return job
        except IntegrityError:
            await self.session.rollback()
            raise AlreadyExistsException(f"Job with ID {job.id} already exists")

    async def getById(self, current_user: UserInfo, job_id: int) -> Job:
        accessible_workspace_ids = self._accessible_workspace_ids(current_user)

        query = select(Job).where(
            (Job.id == job_id)
            & (Job.workspace_id.in_(accessible_workspace_ids))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        job = result.scalar_one_or_none()

        if not job:
            raise NotFoundException(f"Job with id {job_id} not found")

        return job

    async def update(
        self,
        current_user: UserInfo,
        job_id: int,
        job_data: JobPatch,
        ignore_permissions: bool = False,
    ) -> Job:
        if ignore_permissions:
            query = (
                update(Job)
                .where(Job.id == job_id)
                .values(**job_data.model_dump(exclude_unset=True))
            )
            result = await self.session.execute(query)
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise NotFoundException(f"Update failed for job id {job_id}")
            await self.session.commit()
            return await self._getJobById(job_id)
        else:
            accessible_workspace_ids = self._accessible_workspace_ids(current_user)

            query = (
                update(Job)
                .where(
                    (Job.id == job_id)
                    & (Job.workspace_id.in_(accessible_workspace_ids))  # type: ignore[attr-defined]
                )
                .values(**job_data.model_dump(exclude_unset=True))
            )

            result = await self.session.execute(query)

            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise NotFoundException(f"Update failed for job id {job_id}")

            await self.session.commit()
            return await self._getJobById(job_id)

    async def delete(self, current_user: UserInfo, job_id: int) -> None:
        accessible_workspace_ids = self._accessible_workspace_ids(current_user)

        query = delete(Job).where(
            (Job.id == job_id)
            & (Job.workspace_id.in_(accessible_workspace_ids))  # type: ignore[attr-defined]
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise NotFoundException(f"Job delete failed for id {job_id}")

        await self.session.commit()

    async def getWorkspaceJobs(
        self, current_user: UserInfo, workspace_id: int
    ) -> list[Job]:
        if not current_user.isWorkspaceContributor(workspace_id):
            raise ForbiddenException(
                "User does not have permissions to view jobs in that workspace."
            )

        query = select(Job).where(Job.workspace_id == workspace_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _getJobById(self, job_id: int) -> Job:
        query = select(Job).where(Job.id == job_id)
        result = await self.session.execute(query)
        job = result.scalar_one_or_none()

        if not job:
            raise NotFoundException(f"Job with id {job_id} not found")

        return job
