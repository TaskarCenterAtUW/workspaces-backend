from uuid import UUID

from sqlalchemy import delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import NotFoundException
from api.core.security import UserInfo
from api.src.users.schemas import (
    User,
    WorkspaceUserRole,
    WorkspaceUserRoleItem,
    WorkspaceUserRoleType,
)


class UserRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def getUsersForWorkspace(
        self,
        workspace_id: int,
    ) -> list[WorkspaceUserRoleItem]:
        query = (
            select(User, WorkspaceUserRole.role)
            .join(WorkspaceUserRole, User.auth_uid == WorkspaceUserRole.user_auth_uid)
            .where(WorkspaceUserRole.workspace_id == workspace_id)
        )
        result = await self.session.execute(query)

        return [
            WorkspaceUserRoleItem(
                id=user.id,
                auth_uid=user.auth_uid,
                email=user.email,
                display_name=user.display_name,
                role=role,
            )
            for user, role in result.all()
        ]

    async def get_current_user(self, current_user: UserInfo) -> User:
        result = await self.session.exec(
            select(User).where(User.auth_uid == str(current_user.user_uuid))
        )

        # Current user should exist--throw if it doesn't:
        return result.scalar_one()

    async def addUserToWorkspaceWithRole(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: UUID,
        role: WorkspaceUserRoleType,
    ) -> None:
        # Ensure the user has a local user record (signed in at least once):
        user_exists = await self.session.scalar(
            select(User.id).where(User.auth_uid == str(user_id))
        )
        if not user_exists:
            raise NotFoundException(
                f"User {user_id} has not signed in to Workspaces yet"
            )

        # Update role if the user already has one for this workspace:
        result = await self.session.execute(
            update(WorkspaceUserRole)
            .where(
                (WorkspaceUserRole.user_auth_uid == str(user_id))
                & (WorkspaceUserRole.workspace_id == workspace_id)
            )
            .values(role=role)
        )

        if result.rowcount == 0:
            self.session.add(
                WorkspaceUserRole(
                    user_auth_uid=str(user_id),
                    workspace_id=workspace_id,
                    role=role,
                )
            )

        await self.session.commit()

    async def removeUserFromWorkspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: UUID,
    ) -> None:
        query = delete(WorkspaceUserRole).where(
            (WorkspaceUserRole.workspace_id == workspace_id)
            & (WorkspaceUserRole.user_auth_uid == str(user_id))
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:
            raise NotFoundException(
                f"No role assigned for workspace {workspace_id}, user {user_id}"
            )

        await self.session.commit()

    async def deleteRolesForWorkspace(self, workspace_id: int) -> None:
        await self.session.execute(
            delete(WorkspaceUserRole).where(
                WorkspaceUserRole.workspace_id == workspace_id
            )
        )
