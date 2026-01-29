from typing import cast

from sqlalchemy import delete, select, update, text, CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.exceptions import AlreadyExistsException, NotFoundException
from api.core.security import UserInfo
from api.src.workspaces.schemas import (
    QuestDefinitionType,
    Workspace,
    WorkspaceImagery,
    WorkspaceLongQuest,
    WorkspaceUserRoleType,
)

class WorkspaceRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, current_user: UserInfo, workspace_data: Workspace
    ) -> Workspace:
        workspace = Workspace(
            **workspace_data.model_dump(),
            createdBy=current_user.user_uuid, # type: ignore[reportArgumentType]
            createdByName=current_user.user_name,
        )

        try:
            if workspace.tdeiProjectGroupId not in current_user.getProjectGroupIds():
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

    async def getById(self, current_user: UserInfo, workspace_id: int) -> Workspace:
        query = select(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()

        if not workspace:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")
        return workspace

    async def getAll(self, current_user: UserInfo) -> list[Workspace]:
        query = select(Workspace).where(
            Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()) # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(
        self,
        current_user: UserInfo,
        workspace_id: int,
        workspace_data,
    ) -> Workspace:
        update_data = workspace_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        query = (
            update(Workspace)
            .where(
                (Workspace.id == workspace_id)
                & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
            )
            .values(**update_data)
        )
        result = await self.session.execute(query)

        if result.rowcount != 1:
            raise NotFoundException(f"Update failed for workspace id {workspace_id}")

        await self.session.commit()
        return await self.getById(current_user, workspace_id)

    async def createLongformQuest(
        self,
        current_user: UserInfo,
        workspace_id: int,
        longform_quest_data,
    ) -> Workspace | None:
        query = select(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.longFormQuestDef = WorkspaceLongQuest(
                **longform_quest_data.model_dump(),
                modifiedBy=current_user.user_uuid, # type: ignore[reportArgumentType]
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
        longform_quest_data,
    ) -> Workspace:
        update_data = longform_quest_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        update_data["workspace_id"] = workspace_id
        update_data["modifiedBy"] = current_user.user_uuid
        update_data["modifiedByName"] = current_user.user_name

        update_data["type"] = QuestDefinitionType[longform_quest_data.type.name if longform_quest_data.type else "NONE"].value

        query = (
            update(WorkspaceLongQuest)
            .values(**update_data)
            .where(WorkspaceLongQuest.workspace_id == workspace_id) # type: ignore[reportArgumentType]
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        await self.session.commit()
        return await self.getById(current_user, workspace_id)

    async def createImageryDef(
        self,
        current_user: UserInfo,
        workspace_id: int,
        imagery_def_data,
    ) -> Workspace | None:
        query = select(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.imageryListDef = WorkspaceImagery(
                **imagery_def_data.model_dump(exclude_unset=True),
                modifiedBy=current_user.user_uuid, # type: ignore[reportArgumentType]
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
        imagery_def_data,
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
            .where(WorkspaceImagery.workspace_id == workspace_id) # type: ignore[reportArgumentType]
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:
            raise NotFoundException(f"Update failed for workspace id {workspace_id}")

        await self.session.commit()
        return await self.getById(current_user, workspace_id)

    async def delete(self, current_user: UserInfo, workspace_id: int) -> None:
        query = delete(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:
            raise NotFoundException(f"Workspace delete failed for id {workspace_id}")

        await self.session.commit()


class OSMRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def getWorkspaceBBox(
        self,
        current_user: UserInfo,
        workspace_id: int,
    ):
        await self.session.execute(text(f"SET search_path TO 'workspace-{workspace_id}', public"))

        sql_query = text('select MAX(latitude) AS max_lat, MAX(longitude) AS max_lon, \
                         MIN(latitude) AS min_lat, MIN(longitude) AS min_lon from nodes')

        result = await self.session.execute(sql_query)        
        retVal = result.mappings().first()

        if retVal is None:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        return retVal
    
    async def getAllUsers(
        self,
    ):
        await self.session.execute(text("SET search_path TO public"))

        sql_query = text('select id, email, display_name from users')

        result = await self.session.execute(sql_query)        
        return result.mappings().all()

    async def addUserToWorkspaceWithRole(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: int,
        role: WorkspaceUserRoleType,
    ) -> bool:
        await self.session.execute(text("SET search_path TO public"))

        sql_query = text('insert into user_workspace_roles (workspace_id, user_id, role) values \
                         (:workspace_id, :user_id, :role)').bindparams(workspace_id=workspace_id, user_id=user_id, role=role.value)

        result = cast(CursorResult, await self.session.execute(sql_query))
        return result.rowcount != 1

    async def removeUserFromWorkspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: int,
    ) -> bool:
        await self.session.execute(text("SET search_path TO public"))

        sql_query = text('delete from user_workspace_roles where workspace_id = :workspace_id \
                         and user_id = :user_id').bindparams(workspace_id=workspace_id, user_id=user_id)

        result = cast(CursorResult, await self.session.execute(sql_query))
        return result.rowcount != 1
