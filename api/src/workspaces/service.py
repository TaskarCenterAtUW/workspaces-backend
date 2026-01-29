from api.core.security import UserInfo
from api.src.workspaces.models import (
    WorkspaceCreate,
    WorkspaceImageryResponse,
    WorkspaceImageryUpdate,
    WorkspaceLongQuestResponse,
    WorkspaceLongQuestUpdate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from api.src.workspaces.repository import OSMRepository, WorkspaceRepository
from api.src.workspaces.schemas import WorkspaceUserRoleType


class WorkspaceService:

    def __init__(self, repository: WorkspaceRepository):
        self.repository = repository

    async def create_workspace(
        self, current_user: UserInfo, workspace_data: WorkspaceCreate
    ) -> WorkspaceResponse:

        workspace = await self.repository.create(current_user, workspace_data)
        return WorkspaceResponse.model_validate(workspace)

    async def get_workspace(
        self, current_user: UserInfo, workspace_id: int
    ) -> WorkspaceResponse:
        workspace = await self.repository.get_by_id(current_user, workspace_id)
        return WorkspaceResponse.model_validate(workspace)

    async def get_all_workspaces(
        self,
        current_user: UserInfo,
    ) -> list[WorkspaceResponse]:
        workspaces = await self.repository.get_all(current_user)
        return [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]

    async def update_workspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        workspace_data: WorkspaceUpdate,
    ) -> WorkspaceResponse:
        workspace = await self.repository.update(
            current_user, workspace_id, workspace_data
        )
        return WorkspaceResponse.model_validate(workspace)

    async def delete_workspace(self, current_user: UserInfo, workspace_id: int) -> None:
        await self.repository.delete(current_user, workspace_id)

    async def set_longform_quest(
        self,
        current_user: UserInfo,
        workspace_id: int,
        longform_quest_data: WorkspaceLongQuestUpdate,
    ) -> WorkspaceLongQuestResponse:
        workspace = await self.repository.get_by_id(current_user, workspace_id)

        update_data = longform_quest_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")
        
        if workspace.longFormQuestDef:
            workspace = await self.repository.updateLongformQuest(
                current_user, workspace_id, **update_data
            )
        else:
            workspace = await self.repository.createLongformQuest(
                current_user, workspace_id, longform_quest_data
            )

        return WorkspaceLongQuestResponse.model_validate(workspace.longFormQuestDef)

    async def set_imagery(
        self,
        current_user: UserInfo,
        workspace_id: int,
        imagery_data: WorkspaceImageryUpdate,
    ) -> WorkspaceImageryResponse:
        workspace = await self.repository.get_by_id(current_user, workspace_id)

        update_data = imagery_data.model_dump(exclude_unset=True)
        if not update_data:
            raise ValueError("No fields to update")

        if workspace.imageryListDef:
            workspace = await self.repository.updateImageryDef(
                current_user, workspace_id, **update_data
            )
        else:
            workspace = await self.repository.createImageryDef(
                current_user, workspace_id, imagery_data
            )

        return WorkspaceImageryResponse.model_validate(workspace.imageryListDef)


class OSMService:

    def __init__(self, repository: OSMRepository):
        self.repository = repository

    async def get_workspace_bbox(
        self,
        current_user: UserInfo,
        workspace_id: int,
    ):
        return await self.repository.getWorkspaceBBox(current_user, workspace_id)


    async def get_all_users(
        self,
        current_user: UserInfo,
    ):
        return await self.repository.getAllUsers()

    async def add_user_to_workspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: int,
        role: WorkspaceUserRoleType,
    ):
        return await self.repository.addUserToWorkspaceWithRole(current_user, workspace_id, user_id, role)

    async def remove_user_from_workspace(
        self,
        current_user: UserInfo,
        workspace_id: int,
        user_id: int,
    ):
        return await self.repository.removeUserFromWorkspace(current_user, workspace_id, user_id)
