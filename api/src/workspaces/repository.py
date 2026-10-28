from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer, noload
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import (
    AlreadyExistsException,
    ForbiddenException,
    NotFoundException,
)
from api.core.json_schema import get_http_client
from api.core.security import UserInfo, fetch_tdei_dataset
from api.src.workspaces.schemas import (
    ExternalAppsDefinitionType,
    ImagerySettingsPatch,
    QuestDefinitionType,
    QuestSettingsPatch,
    Workspace,
    WorkspaceCreate,
    WorkspaceCreateCheck,
    WorkspaceCreateCheckResponse,
    WorkspaceImagery,
    WorkspaceLongQuest,
    WorkspaceNameCheck,
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
        if workspace_data.isTDEIDataset():
            importStatus = "in-progress"
        workspace = Workspace(
            **workspace_data.model_dump(),
            createdBy=current_user.user_uuid,  # type: ignore[reportArgumentType]
            createdByName=current_user.user_name,
            importStatus=importStatus,
        )

        await self._authorize_create(
            current_user,
            str(workspace.tdeiProjectGroupId),
            workspace_data.type,
            workspace_data.tdeiRecordId,
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

    # @test: Test that gig_only limits the list to OSW workspaces with external app access (public or project group), and that without it every type and access level is returned
    async def getAll(
        self, current_user: UserInfo, gig_only: bool = False
    ) -> list[Workspace]:
        query = (
            select(Workspace).where(
                Workspace.tdeiProjectGroupId.in_(current_user.getProjectGroupIds())  # type: ignore[attr-defined]
            )
            # The list response (WorkspaceSummaryResponse) sends none of these,
            # so don't load them: the geometry can be a large multipolygon, the
            # TDEI metadata a whole dataset record, and the quest and imagery
            # definitions are otherwise joined in for every workspace.
            .options(
                defer(Workspace.geometry),  # pyright: ignore[reportArgumentType]
                defer(Workspace.tdeiMetadata),  # pyright: ignore[reportArgumentType]
                noload(
                    Workspace.longFormQuestDef  # pyright: ignore[reportArgumentType]
                ),
                noload(Workspace.imageryListDef),  # pyright: ignore[reportArgumentType]
            )
        )
        # The mobile apps (ScoutRoute) only show OSW workspaces opened to
        # external apps, so filter here instead of sending the rest to discard.
        if gig_only:
            is_osw = Workspace.type == WorkspaceType.OSW
            open_to_apps = (
                Workspace.externalAppAccess != ExternalAppsDefinitionType.NONE
            )
            query = query.where(
                is_osw, open_to_apps  # pyright: ignore[reportArgumentType]
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    # The checks `create` applies before inserting, shared with
    # `check_create_permission` so the pre-flight answer can't drift from it.
    async def _authorize_create(
        self,
        current_user: UserInfo,
        project_group_id: str,
        workspace_type: WorkspaceType,
        tdei_record_id: UUID | None,
    ) -> None:
        if not current_user.canCreateWorkspace(project_group_id, workspace_type):
            raise ForbiddenException(
                "User does not have permissions to create a workspace of that "
                "type in that project group."
            )

        if tdei_record_id is not None:
            dataset = await fetch_tdei_dataset(
                str(tdei_record_id), current_user.credentials
            )
            if dataset is None:
                raise NotFoundException(f"TDEI dataset {tdei_record_id} not found")
            # Otherwise an OSW generator could open a Flex or Pathways
            # dataset as an OSW workspace, getting around the role rule.
            if not dataset.matches_type(workspace_type):
                raise ForbiddenException(
                    f"TDEI dataset {tdei_record_id} is a "
                    f"{dataset.data_type or 'unknown'} dataset and can't be opened "
                    f"as a {workspace_type.value} workspace."
                )
            if not current_user.canCreateWorkspaceFromDataset(
                project_group_id, workspace_type, dataset
            ):
                raise ForbiddenException(
                    "An unreleased dataset can only be opened in a workspace "
                    "in its own project group."
                )

    # @test: Test that check_create_permission allows what create allows, and reports a reason (instead of raising) for a missing role, an unknown dataset, a dataset whose data type differs from the workspace type, and an unreleased dataset from another project group
    # @test: Test that check_create_permission lets a 502 from the TDEI dataset lookup propagate rather than reporting canCreate false
    # A TDEI failure (502) is not a denial, so it propagates: answering
    # canCreate=false would tell the user they lack permission when TDEI is
    # simply down.
    async def check_create_permission(
        self, current_user: UserInfo, check: WorkspaceCreateCheck
    ) -> WorkspaceCreateCheckResponse:
        try:
            await self._authorize_create(
                current_user,
                str(check.tdeiProjectGroupId),
                check.type,
                check.tdeiRecordId,
            )
        except (ForbiddenException, NotFoundException) as e:
            return WorkspaceCreateCheckResponse(canCreate=False, reason=e.detail)
        return WorkspaceCreateCheckResponse(canCreate=True)

    async def is_name_available(
        self, current_user: UserInfo, workspace_name_check: WorkspaceNameCheck
    ) -> bool:
        if (
            str(workspace_name_check.tdeiProjectGroupId)
            not in current_user.getProjectGroupIds()
        ):
            raise ForbiddenException(
                "User does not have permissions to check workspace names in that "
                "project group."
            )

        query = (
            select(func.count())
            .select_from(Workspace)
            .where(
                (
                    Workspace.title == workspace_name_check.title
                )  # pyright: ignore[reportArgumentType]
                & (
                    Workspace.tdeiProjectGroupId
                    == workspace_name_check.tdeiProjectGroupId
                )
            )
        )
        result_count = await self.session.scalar(query)

        # If at least one workspace exists with the same title, the name is not available.
        return result_count == 0

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
