from typing import Any
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.exceptions import AlreadyExistsException, NotFoundException
from api.src.workspaces.models import Workspace
from api.src.workspaces.schemas import WorkspaceCreate, WorkspaceUpdate


class WorkspaceRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, projectGroupIds: list[str], workspace_data: WorkspaceCreate
    ) -> Workspace:
        workspace = Workspace(**workspace_data.model_dump())
        try:
            if workspace.tdeiProjectGroupId not in projectGroupIds:
                raise ValueError(
                    "User does not have permissions to create a workspace in that project group."
                )

            self.session.add(workspace)
            await self.session.commit()
            await self.session.refresh(workspace)
            return workspace
        except IntegrityError:
            await self.session.rollback()
            raise AlreadyExistsException(
                f"Workspace with ID {workspace_data.id} already exists"
            )

    async def get_by_id(
        self, projectGroupIds: list[str], workspace_id: int
    ) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(projectGroupIds)
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")
        return workspace

    async def get_all(self, projectGroupIds: list[str]) -> list[Workspace]:
        query = select(Workspace).where(
            Workspace.tdeiProjectGroupId.in_(projectGroupIds)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(
        self,
        projectGroupIds: list[str],
        workspace_id: int,
        workspace_data: Any,
    ) -> Workspace:
        update_data = workspace_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        query = (
            update(Workspace)
            .where(
                Workspace.id == workspace_id
                and Workspace.tdeiProjectGroupId.in_(projectGroupIds)
            )
            .values(**update_data)
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
        return await self.get_by_id(projectGroupIds, workspace_id)

    async def delete(self, projectGroupIds: list[str], workspace_id: int) -> None:
        query = delete(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(projectGroupIds)
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
