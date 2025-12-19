from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)


class WorkspaceService:

    def __init__(self, repository: WorkspaceRepository):
        self.repository = repository

    async def create_workspace(
        self, projectGroupIds: list[str], workspace_data: WorkspaceCreate
    ) -> WorkspaceResponse:
        workspace = await self.repository.create(projectGroupIds, workspace_data)
        return WorkspaceResponse.model_validate(workspace)

    async def get_workspace(
        self, projectGroupIds: list[str], workspace_id: int
    ) -> WorkspaceResponse:
        workspace = await self.repository.get_by_id(projectGroupIds, workspace_id)
        return WorkspaceResponse.model_validate(workspace)

    async def get_all_workspaces(
        self,
        projectGroupIds: list[str],
    ) -> list[WorkspaceResponse]:
        workspaces = await self.repository.get_all(projectGroupIds)
        return [WorkspaceResponse.model_validate(workspace) for workspace in workspaces]

    async def update_workspace(
        self,
        projectGroupIds: list[str],
        workspace_id: int,
        workspace_data: WorkspaceUpdate,
    ) -> WorkspaceResponse:
        workspace = await self.repository.update(
            projectGroupIds, workspace_id, workspace_data
        )
        return WorkspaceResponse.model_validate(workspace)

    async def delete_workspace(
        self, projectGroupIds: list[str], workspace_id: int
    ) -> None:
        await self.repository.delete(projectGroupIds, workspace_id)
