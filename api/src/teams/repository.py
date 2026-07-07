# SQLModel declares columns as plain annotations (e.g. ``id: int | None``) rather
# than ``Mapped[int]``, so Pyright reads ``Column == value`` as a ``bool`` and
# misjudges ``where()``/``exec()``/``select()``/``selectinload`` calls. These are
# framework false positives; the queries are valid at runtime.
# pyright: reportArgumentType=false, reportCallIssue=false, reportAttributeAccessIssue=false

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import NotFoundException
from api.src.teams.schemas import (
    WorkspaceTeam,
    WorkspaceTeamCreate,
    WorkspaceTeamItem,
    WorkspaceTeamUpdate,
)
from api.src.users.schemas import User


class WorkspaceTeamRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self, workspace_id: int) -> list[WorkspaceTeamItem]:
        result = await self.session.exec(
            select(WorkspaceTeam)
            .options(selectinload(WorkspaceTeam.users))
            .where(WorkspaceTeam.workspace_id == workspace_id)
        )

        return [WorkspaceTeamItem.from_team(x) for x in result.scalars().all()]

    async def get(self, id: int, load_members: bool = False) -> WorkspaceTeam:
        query = select(WorkspaceTeam).where(WorkspaceTeam.id == id)

        if load_members:
            query = query.options(selectinload(WorkspaceTeam.users))

        result = await self.session.exec(query)
        team = result.scalar_one_or_none()

        if not team:
            raise NotFoundException(f"Team {id} not found")

        return team

    async def get_item(self, id: int) -> WorkspaceTeamItem:
        team = await self.get(id, load_members=True)
        item = WorkspaceTeamItem.from_team(team)

        return item

    async def assert_team_in_workspace(self, id: int, workspace_id: int):
        team_exists = await self.session.scalar(
            select(
                exists()
                .where(WorkspaceTeam.id == id)
                .where(WorkspaceTeam.workspace_id == workspace_id)
            )
        )

        if not team_exists:
            raise NotFoundException(f"Team {id} not in workspace {workspace_id}")

    async def create(self, workspace_id: int, data: WorkspaceTeamCreate) -> int:
        team = WorkspaceTeam(name=data.name, workspace_id=workspace_id)

        self.session.add(team)
        await self.session.commit()
        await self.session.refresh(team)

        assert team.id is not None
        return team.id

    async def update(self, id: int, data: WorkspaceTeamUpdate):
        team = await self.get(id)
        team.name = data.name

        self.session.add(team)
        await self.session.commit()

    async def delete(self, id: int) -> None:
        await self.session.execute(delete(WorkspaceTeam).where(WorkspaceTeam.id == id))  # type: ignore[arg-type]
        await self.session.commit()

    async def get_members(self, id: int) -> list[User]:
        result = await self.session.exec(select(User).where(User.teams.any(id=id)))

        return result.scalars().all()

    async def add_member(self, id: int, user_id: int):
        user_result = await self.session.exec(
            select(User).options(selectinload(User.teams)).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()

        if not user:
            raise NotFoundException(f"User {user_id} does not exist")

        team = await self.get(id)

        if team in user.teams:
            return

        user.teams.append(team)
        self.session.add(user)
        await self.session.commit()

    async def remove_member(self, id: int, user_id: int):
        user_result = await self.session.exec(
            select(User).options(selectinload(User.teams)).where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()

        if not user:
            raise NotFoundException(f"User {user_id} does not exist")

        team = await self.get(id)

        if team not in user.teams:
            return

        user.teams.remove(team)
        self.session.add(user)
        await self.session.commit()
