from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import AlreadyExistsException, NotFoundException
from api.core.security import UserInfo
from api.src.workspaces.schemas import (
    QuestDefinitionType,
    User,
    Workspace,
    WorkspaceImagery,
    WorkspaceLongQuest,
    WorkspaceUserRole,
    WorkspaceUserRoleType,
)


class WorkspaceRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, current_user: UserInfo, workspace_data: dict[str, Any]
    ) -> Workspace:
        workspace = Workspace(
            **workspace_data,
            createdBy=current_user.user_uuid,  # type: ignore[reportArgumentType]
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
                f"Workspace with ID {workspace.id} already exists"
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
            Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds())  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update(
        self,
        current_user: UserInfo,
        workspace_id: int,
        workspace_data: dict[str, Any],
    ) -> Workspace:
        query = (
            update(Workspace)
            .where(
                (Workspace.id == workspace_id)
                & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
            )
            .values(**workspace_data)
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
        longform_quest_data: dict[str, Any],
    ) -> Workspace | None:
        query = select(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.longFormQuestDef = WorkspaceLongQuest(
                **longform_quest_data,
                modifiedBy=current_user.user_uuid,  # type: ignore[reportArgumentType]
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
        longform_quest_data: dict[str, Any],
    ) -> Workspace:
        update_data = longform_quest_data
        update_data["modifiedBy"] = current_user.user_uuid
        update_data["modifiedByName"] = current_user.user_name

        quest_type = longform_quest_data.get("type")
        update_data["type"] = QuestDefinitionType[
            quest_type.name if quest_type else "NONE"
        ].value

        query = (
            update(WorkspaceLongQuest)
            .values(**update_data)
            .where(WorkspaceLongQuest.workspace_id == workspace_id)  # type: ignore[reportArgumentType]
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
        imagery_def_data: dict[str, Any],
    ) -> Workspace | None:
        query = select(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )
        result = await self.session.execute(query)
        workspace = result.scalar_one_or_none()
        if workspace:
            workspace.imageryListDef = WorkspaceImagery(
                **imagery_def_data,
                modifiedBy=current_user.user_uuid,  # type: ignore[reportArgumentType]
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
        imagery_def_data: dict[str, Any],
    ) -> Workspace:
        update_data = imagery_def_data
        update_data["modifiedBy"] = current_user.user_uuid
        update_data["modifiedByName"] = current_user.user_name

        query = (
            update(WorkspaceImagery)
            .values(**update_data)
            .where(WorkspaceImagery.workspace_id == workspace_id)  # type: ignore[reportArgumentType]
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
        await self.session.execute(
            text(f"SET search_path TO 'workspace-{workspace_id}', public")
        )

        sql_query = text(
            "select MAX(latitude) AS max_lat, MAX(longitude) AS max_lon, \
                         MIN(latitude) AS min_lat, MIN(longitude) AS min_lon from nodes"
        )

        result = await self.session.execute(sql_query)
        retVal = result.mappings().first()

        if retVal is None:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        return retVal

    async def getAllUsers(
        self,
    ):
        query = select(User)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def addUserToWorkspaceWithRole(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: UUID,
        role: WorkspaceUserRoleType,
    ) -> None:

        userRole = WorkspaceUserRole(
            auth_user_uid=cast(UUID, current_user.user_uuid),
            workspace_id=workspace_id,
            role=role,
        )

        try:
            self.session.add(userRole)
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise AlreadyExistsException(
                "User association with that workspace already exists"
            )

    async def removeUserFromWorkspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: UUID,
    ) -> None:
        query = delete(WorkspaceUserRole).where(
            (WorkspaceUserRole.workspace_id == workspace_id)  # type: ignore[reportArgumentType]
            & (WorkspaceUserRole.auth_user_uid == user_id)
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:
            raise NotFoundException(
                f"User association removal failed for workspace {workspace_id} and user {user_id}"
            )

        await self.session.commit()
