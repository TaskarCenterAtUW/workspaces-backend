from sqlalchemy import delete, select, update, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.exceptions import AlreadyExistsException, NotFoundException
from api.core.security import UserInfo
from api.src.workspaces.models import (
    WorkspaceCreate,
    WorkspaceImageryUpdate,
    WorkspaceLongQuestUpdate,
    WorkspaceUpdate,
)
from api.src.workspaces.schemas import (
    QuestDefinitionTypeDB,
    Workspace,
    WorkspaceImagery,
    WorkspaceLongQuest,
)

class WorkspaceRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, current_user: UserInfo, workspace_data: WorkspaceCreate
    ) -> Workspace:
        workspace = Workspace(
            **workspace_data.model_dump(),
            createdBy=current_user.user_uuid,
            createdByName=current_user.user_name,
        )

        try:
            if workspace.tdeiProjectGroupId not in current_user.projectGroups:
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

    async def get_by_id(self, current_user: UserInfo, workspace_id: int) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")
        return workspace

    async def get_all(self, current_user: UserInfo) -> list[Workspace]:
        query = select(Workspace).where(
            Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # FIXME: should we be tracking modifiedBy and modifiedByName here?
    async def update(
        self,
        current_user: UserInfo,
        workspace_id: int,
        workspace_data: WorkspaceUpdate,
    ) -> Workspace:
        update_data = workspace_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        query = (
            update(Workspace)
            .where(
                Workspace.id == workspace_id
                and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
            )
            .values(**update_data)
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
        return await self.get_by_id(current_user, workspace_id)

    async def createLongformQuest(
        self,
        current_user: UserInfo,
        workspace_id: int,
        longform_quest_data: WorkspaceLongQuestUpdate,
    ) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.longFormQuestDef = WorkspaceLongQuest(
                **longform_quest_data.model_dump(),
                modifiedBy=current_user.user_uuid,
                modifiedByName=current_user.user_name,
                workspace_id=workspace_id,
            )
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def updateLongformQuest(
        self,
        current_user: UserInfo,
        workspace_id: int,
        longform_quest_data: WorkspaceLongQuestUpdate,
    ) -> Workspace:
        update_data = longform_quest_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        update_data["workspace_id"] = workspace_id
        update_data["modifiedBy"] = current_user.user_uuid
        update_data["modifiedByName"] = current_user.user_name

        # map the type from model enum to DB enum
        # FIXME: this hack is necessary because the UI and the DB don't use the same values, fix that?
        update_data["type"] = QuestDefinitionTypeDB[
            longform_quest_data.type or "NONE"
        ].value

        query = (
            update(WorkspaceLongQuest)
            .values(**update_data)
            .where(Workspace.id == WorkspaceLongQuest.workspace_id)
            .where(
                Workspace.id == workspace_id
                and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
            )
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
        return await self.get_by_id(current_user, workspace_id)

    async def createImageryDef(
        self,
        current_user: UserInfo,
        workspace_id: int,
        imagery_def_data: WorkspaceImageryUpdate,
    ) -> Workspace:
        query = select(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.imageryListDef = WorkspaceImagery(
                **imagery_def_data.model_dump(exclude_unset=True),
                modifiedBy=current_user.user_uuid,
                modifiedByName=current_user.user_name,
                workspace_id=workspace_id,
            )
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def updateImageryDef(
        self,
        current_user: UserInfo,
        workspace_id: int,
        imagery_def_data: WorkspaceImageryUpdate,
    ) -> Workspace:
        update_data = imagery_def_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        update_data["workspace_id"] = workspace_id
        update_data["modifiedBy"] = current_user.user_uuid
        update_data["modifiedByName"] = current_user.user_name

        query = (
            update(WorkspaceImagery)
            .values(**update_data)
            .where(Workspace.id == WorkspaceImagery.workspace_id)
            .where(
                Workspace.id == workspace_id
                and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
            )
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
        return await self.get_by_id(current_user, workspace_id)

    async def delete(self, current_user: UserInfo, workspace_id: int) -> None:
        query = delete(Workspace).where(
            Workspace.id == workspace_id
            and Workspace.tdeiProjectGroupId.in_(current_user.projectGroups)
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()


class OSMRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def getWorkspaceBBox(
        self,
        current_user: UserInfo,
        workspace_id: int,
    ):
        sql_query = text('select MAX(latitude) AS max_lat, MAX(longitude) AS max_lon, MIN(latitude) AS min_lat, MIN(longitude) AS min_lon')
        result = await self.session.execute(sql_query)
        return result.first()
