from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import (
    AlreadyExistsException,
    ForbiddenException,
    NotFoundException,
)
from api.core.json_schema import get_http_client
from api.core.security import UserInfo
from api.src.workspaces.schemas import (
    ImagerySettingsPatch,
    QuestDefinitionType,
    QuestSettingsPatch,
    Workspace,
    WorkspaceCreate,
    WorkspaceImagery,
    WorkspaceLongQuest,
    WorkspacePatch,
    WorkspaceType,
)


class WorkspaceRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, current_user: UserInfo, workspace_data: WorkspaceCreate
    ) -> Workspace:
        importStatus = "NA"
        if (
            workspace_data.type == WorkspaceType.OSW
            and workspace_data.tdeiRecordId is not None
        ):
            importStatus = "in-progress"
        workspace = Workspace(
            **workspace_data.model_dump(),
            createdBy=current_user.user_uuid,  # type: ignore[reportArgumentType]
            createdByName=current_user.user_name,
            importStatus=importStatus,
        )

        if str(workspace.tdeiProjectGroupId) not in current_user.getProjectGroupIds():
            raise ForbiddenException(
                "User does not have permissions to create a workspace in that "
                "project group."
            )

        try:
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
        workspace_data: WorkspacePatch,
    ) -> Workspace:
        query = (
            update(Workspace)
            .where(
                (Workspace.id == workspace_id)
                & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
            )
            .values(**workspace_data.model_dump(exclude_unset=True))
        )
        result = await self.session.execute(query)

        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise NotFoundException(f"Update failed for workspace id {workspace_id}")

        await self.session.commit()
        return await self.getById(current_user, workspace_id)

    async def save_longform_quest(
        self,
        current_user: UserInfo,
        workspace_id: int,
        longform_quest_data: QuestSettingsPatch,
    ) -> None:
        query = (
            update(WorkspaceLongQuest)
            .values(
                type=QuestDefinitionType[longform_quest_data.type].value,
                definition=longform_quest_data.definition,
                url=longform_quest_data.url,
                modifiedBy=current_user.user_uuid,
                modifiedByName=current_user.user_name,
            )
            .where(
                WorkspaceLongQuest.workspace_id
                == workspace_id  # pyright: ignore[reportArgumentType]
            )
        )
        result = await self.session.execute(query)

        if result.rowcount == 0:  # pyright: ignore[reportAttributeAccessIssue]
            self.session.add(
                WorkspaceLongQuest(  # pyright: ignore[reportCallIssue]
                    workspace_id=workspace_id,
                    type=QuestDefinitionType[longform_quest_data.type].value,
                    definition=longform_quest_data.definition,
                    url=longform_quest_data.url,
                    modifiedBy=current_user.user_uuid,
                    modifiedByName=current_user.user_name,
                )
            )

        await self.session.commit()

    @staticmethod
    async def resolve_quest_def(quest: WorkspaceLongQuest | None) -> str | None:
        """
        Resolve a WorkspaceLongQuest to its raw JSON string content.

        - JSON type: returns the stored definition string
        - URL type: fetches and returns the remote content
        - NONE or missing: returns None
        """

        if quest is None or quest.type == QuestDefinitionType.NONE:
            return None
        if quest.type == QuestDefinitionType.JSON:
            return quest.definition or None

        if quest.url:
            client = get_http_client()
            if client is None:
                return None
            try:
                response = await client.get(quest.url, timeout=10)
                return response.text
            except Exception:
                return None

        return None

    async def save_imagery_def(
        self,
        current_user: UserInfo,
        workspace_id: int,
        imagery_def_data: ImagerySettingsPatch,
    ) -> None:
        query = (
            update(WorkspaceImagery)
            .values(
                definition=imagery_def_data.definition,
                modifiedBy=current_user.user_uuid,
                modifiedByName=current_user.user_name,
            )
            .where(
                WorkspaceImagery.workspace_id
                == workspace_id  # pyright: ignore[reportArgumentType]
            )
        )

        result = await self.session.execute(query)

        if result.rowcount == 0:  # type: ignore[attr-defined]
            self.session.add(
                WorkspaceImagery(  # pyright: ignore[reportCallIssue]
                    workspace_id=workspace_id,
                    definition=imagery_def_data.definition,
                    modifiedBy=current_user.user_uuid,
                    modifiedByName=current_user.user_name,
                )
            )

        await self.session.commit()

    async def delete(self, current_user: UserInfo, workspace_id: int) -> None:
        query = delete(Workspace).where(
            (Workspace.id == workspace_id)
            & (Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds()))  # type: ignore[attr-defined]
        )

        result = await self.session.execute(query)

        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise NotFoundException(f"Workspace delete failed for id {workspace_id}")

        await self.session.commit()
