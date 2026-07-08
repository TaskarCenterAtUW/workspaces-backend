from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    async def get_privileged_workspace_members(
        self,
        workspace_id: int,
    ) -> list[WorkspaceUserRoleItem]:
        # The table only stores "lead" and "validator" role assignments. Project
        # group members implicitly have the base "contributor" role.
        #
        query = (
            select(  # pyright: ignore[reportCallIssue]
                User, WorkspaceUserRole.role  # pyright: ignore[reportArgumentType]
            )
            .join(WorkspaceUserRole, User.auth_uid == WorkspaceUserRole.user_auth_uid)
            .where(WorkspaceUserRole.workspace_id == workspace_id)
        )
        result = await self.session.execute(query)

        return [
            WorkspaceUserRoleItem(
                id=user.id,
                auth_uid=user.auth_uid,
                display_name=user.display_name,
                role=role,
            )
            for user, role in result.all()
        ]

    async def get_current_user(self, current_user: UserInfo) -> User:
        result = await self.session.exec(  # pyright: ignore[reportCallIssue]
            select(User).where(
                User.auth_uid
                == str(current_user.user_uuid)  # pyright: ignore[reportArgumentType]
            )
        )

        # Current user should exist--throw if it doesn't:
        return result.scalar_one()

    async def assign_member_role(
        self,
        workspace_id: int,
        user_id: UUID,
        role: WorkspaceUserRoleType,
    ) -> None:
        # Ensure the user has a local user record (signed in at least once):
        user_exists = await self.session.scalar(
            select(  # pyright: ignore[reportCallIssue]
                User.id  # pyright: ignore[reportArgumentType]
            ).where(  # pyright: ignore[reportCallIssue]
                User.auth_uid == str(user_id)
            )
        )
        if not user_exists:
            raise NotFoundException(
                f"User {user_id} has not signed in to Workspaces yet"
            )

        await self.session.execute(
            pg_insert(WorkspaceUserRole)
            .values(
                user_auth_uid=str(user_id),
                workspace_id=workspace_id,
                role=role,
            )
            .on_conflict_do_update(
                index_elements=["user_auth_uid", "workspace_id"],
                set_={"role": role},
            )
        )
        await self.session.commit()

    async def remove_member_role(
        self,
        workspace_id: int,
        user_id: UUID,
    ) -> None:
        query = delete(WorkspaceUserRole).where(
            (
                WorkspaceUserRole.workspace_id == workspace_id
            )  # pyright: ignore[reportArgumentType]
            & (WorkspaceUserRole.user_auth_uid == str(user_id))
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:  # pyright: ignore[reportAttributeAccessIssue]
            raise NotFoundException(
                f"No role assigned for workspace {workspace_id}, user {user_id}"
            )

        await self.session.commit()

    async def remove_all_member_roles(self, workspace_id: int) -> None:
        await self.session.execute(
            delete(WorkspaceUserRole).where(
                WorkspaceUserRole.workspace_id
                == workspace_id  # pyright: ignore[reportArgumentType]
            )
        )
        await self.session.commit()
