import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.database import get_osm_session, get_task_session
from api.core.json_schema import (
    validate_imagery_definition_schema,
    validate_quest_definition_schema,
)
from api.core.logging import get_logger
from api.core.messenger import Messenger
from api.core.security import UserInfo, evict_user_from_cache, validate_token
from api.src.osm.repository import OSMRepository
from api.src.osm.routes import get_osm_repo
from api.src.tasking.projects.repository import TaskingProjectRepository
from api.src.users.repository import UserRepository
from api.src.users.schemas import WorkspaceUserRoleType
from api.src.workspaces.jobs.repository import JobRepository
from api.src.workspaces.jobs.schemas import Job, JobCreate, JobPatch
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.schemas import (
    ImagerySettingsPatch,
    QuestDefinitionTypeName,
    QuestSettingsPatch,
    QuestSettingsResponse,
    WorkspaceCreate,
    WorkspaceImagery,
    WorkspacePatch,
    WorkspaceResponse,
)

# Set up logger for this module
logger = get_logger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def get_workspace_repository(
    session: AsyncSession = Depends(get_task_session),
) -> WorkspaceRepository:
    repository = WorkspaceRepository(session)
    return repository


def get_user_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> UserRepository:
    return UserRepository(session)


def get_project_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> TaskingProjectRepository:
    return TaskingProjectRepository(session)


def get_jobs_repository(
    session: AsyncSession = Depends(get_osm_session),
) -> JobRepository:
    return JobRepository(session)


# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this method properly calls the repository method to fetch the workspace and that the repository method properly fetches the workspace from the database
# @test: Test that this method properly handles numeric workspace_id input and invalid values for the same
# @test: Test that this method properly checks permissions to see if the user has access to the workspace and if they don't, it doesn't appear in the list
# @test: Test that this method properly handles the case where the workspace does not exist and doesn't include it
# @test: Test that this method properly handles inputs that match the schema in WorkspaceResponse
# @test: Test that this method's results match the values of the fetch-by-workspace-id method below; all workspaces in this list are retrievable via that method


# Returns list of workspaces user has access to as JSON payload on success--returns empty JSON list if none
@router.get("/mine", response_model=list[WorkspaceResponse])
async def get_my_workspaces(
    repository: WorkspaceRepository = Depends(get_workspace_repository),
    user_repo: UserRepository = Depends(get_user_repository),
    project_repo: TaskingProjectRepository = Depends(get_project_repository),
    current_user: UserInfo = Depends(validate_token),
) -> list[WorkspaceResponse]:
    try:
        workspaces = await repository.getAll(current_user)
        workspace_ids = [ws.id for ws in workspaces if ws.id is not None]

        projects_counts = await project_repo.get_projects_counts(workspace_ids)
        members_counts = await user_repo.get_member_counts(workspace_ids)

        responses = []
        for ws in workspaces:
            assert ws.id is not None  # persisted workspace always has an id
            responses.append(
                WorkspaceResponse.from_workspace(
                    ws,
                    current_user,
                    projects_count=projects_counts.get(ws.id, 0),
                    members_count=members_counts.get(ws.id, 0),
                )
            )
        return responses
    except Exception as e:
        logger.error(f"Failed to fetch workspaces: {str(e)}")
        raise


# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this method properly calls the repository method to fetch the workspace and that the repository method properly fetches the workspace from the database
# @test: Test that this method properly handles numeric workspace_id input and invalid values for the same
# @test: Test that this method properly checks permissions to see if the user has access to the workspace and returns a 403 if they do not
# @test: Test that this method properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly handles inputs that match the schema in WorkspaceResponse


# Returns JSON payload or 204 if not found
@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceResponse:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)
        quest_def_str = await WorkspaceRepository.resolve_quest_def(
            workspace.longFormQuestDef
        )
        try:
            quest_def = json.loads(quest_def_str) if quest_def_str else None
        except json.JSONDecodeError:
            quest_def = None

        return WorkspaceResponse.from_workspace(
            workspace,
            current_user,
            imagery_list_def=(
                workspace.imageryListDef.definition
                if workspace.imageryListDef
                else None
            ),
            long_form_quest_def=quest_def,
        )
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this method properly handles numeric workspace_id input and invalid values for the same
# @test: Test taht this workspace returns proper values for a workspace that exists and that the bbox matches the expected values
# @test: Test that this method properly handles the case where the workspace does not exist and returns a 404


@router.get("/{workspace_id}/bbox", response_model=None)
async def get_workspace_bbox(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_osm: OSMRepository = Depends(get_osm_repo),
    current_user: UserInfo = Depends(validate_token),
):
    try:
        # this first query is for permissions checking
        await repository_ws.getById(current_user, workspace_id)
        bbox = await repository_osm.getWorkspaceBBox(workspace_id)
        return bbox
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly evicts any cached data for the user after the workspace is deleted so that their next request reflects the deletion rather than serving stale data for up to an hour
# @test: Test that this method properly calls the repository method to create the workspace and that the repository method properly creates the workspace in the database
# @test: Test that this method properly sets the creator as the lead of the workspace and that the repository method properly assigns the lead role in the database
# @test: Test that this method properly handles inputs that match the schema in WorkspaceCreate and that the repository method properly creates the workspace in the database with those values
# @test: Test that this method properly handles inputs that do not match the schema in WorkspaceCreate and that the repository method properly raises an error and does not update the workspace in the database with those values
# @test: Test that this method won't allow users to modify an existing workspace in any way, or create a workspace with the same workspace_id as an existing one


# Returns 201 on success?
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    workspace_data: WorkspaceCreate,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_users: UserRepository = Depends(get_user_repository),
    jobs_repository: JobRepository = Depends(get_jobs_repository),
    current_user: UserInfo = Depends(validate_token),
) -> dict[str, int | None]:
    try:
        workspace = await repository_ws.create(current_user, workspace_data)
        assert workspace.id is not None  # freshly persisted workspace has an id

        # Assign the creator as lead so that non-POC members can manage their
        # own workspace:
        #
        await repository_users.assign_member_role(
            workspace.id,
            current_user.user_uuid,
            WorkspaceUserRoleType.LEAD,
        )

        # await jobs_repository.create(current_user, workspace.id)
        # Evict the creator's cache so their next request reflects the new
        # workspace and lead role rather than serving stale data for up to
        # an hour:
        #
        job_id = None
        if workspace_data.isTDEIOSWDataset() or workspace_data.isTDEIPathwaysDataset():
            # Get the user access_token from the header
            access_token = current_user.credentials
            request_data = {
                "workspace_id": workspace.id,
                "tdei_dataset_id": (
                    str(workspace_data.tdeiRecordId)
                    if workspace_data.tdeiRecordId is not None
                    else ""
                ),
                "tdei_token": access_token,
                "data_type": workspace_data.type.name.lower(),
            }
            create_job = await jobs_repository.create(
                current_user,
                JobCreate(
                    job_type="workspace-import",
                    status="requested",
                    request=request_data,
                    workspace_id=workspace.id,
                ),
            )
            job_id = create_job.id
            logger.info(
                f"Import job with ID: {job_id} created for workspace ID: {workspace.id}"
            )

            request_data["unique_job_id"] = job_id
            # since this is the first time, we donot have the job_id unless we created one. Update the same in the request
            await jobs_repository.update(
                current_user,
                job_id,
                JobPatch(request=request_data),
                ignore_permissions=True,
            )
            # Send the message over the bus here.
            messenger = Messenger()
            messenger.send_message(
                request_data
            )  # send the message to the bus for processing

        evict_user_from_cache(current_user.user_uuid)
        return {"workspaceId": workspace.id, "importJobId": job_id}
    except Exception as e:
        logger.error(f"Failed to create workspace: {str(e)}")
        raise


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly calls the repository method to update the workspace and that the repository method properly updates the workspace in the database
# @test: Test that this method properly handles numeric workspace_id input and invalid values for the same
# @test: Test that this method properly handles inputs that match the schema in WorkspacePatch and that the repository method properly updates the workspace in the database with those values
# @test: Test that this method properly handles inputs that do not match the schema in WorkspacePatch and that the repository method properly raises an error and does not update the workspace in the database with those values


@router.patch("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_workspace(
    workspace_id: int,
    workspace_data: WorkspacePatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to update this workspace",
        )

    if not workspace_data.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update.",
        )

    try:
        await repository_ws.update(current_user, workspace_id, workspace_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly evicts any cached data for the user after the workspace is deleted so that their next request reflects the deletion rather than serving stale data for up to an hour
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly calls the repository method to delete the workspace and that the repository method properly deletes the workspace from the database
# @test: Test that this method properly handles the case where the workspace has members and that the repository method properly removes all member roles from the database
# @test: Test that this method properly handles numeric workspace_id input and invalid values for the same


# Returns 204 on success
@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    repository_users: UserRepository = Depends(get_user_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to delete this workspace",
        )

    try:
        members = await repository_users.get_privileged_workspace_members(workspace_id)
        await repository_ws.delete(current_user, workspace_id)
        await repository_users.remove_all_member_roles(workspace_id)
        for member in members:
            evict_user_from_cache(UUID(member.auth_uid))
    except Exception as e:
        logger.error(f"Failed to delete workspace {workspace_id}: {str(e)}")
        raise


# QUESTS

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly calls the repository method to fetch the long quest definition

# FIXME: Why are there two methods to fetch the long quest? One for legacy purposes? Can we migrate callers?


# Return the resolved quest definition content as JSON, or 204 if not set:
@router.get("/{workspace_id}/quests/long")
async def get_long_quest_def(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> Response:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)
        definition = await WorkspaceRepository.resolve_quest_def(
            workspace.longFormQuestDef
        )

        if definition is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        return Response(content=definition, media_type="application/json")
    except Exception as e:
        logger.error(
            f"Failed to fetch quest def for workspace {workspace_id}: {str(e)}"
        )
        raise


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly calls the repository method to fetch the long quest definition, and if it's not defined, the default value as defined
#        in this method


# Returns JSON payload or 204 if not set
@router.get(
    "/{workspace_id}/quests/long/settings", response_model=QuestSettingsResponse
)
async def get_long_quest_settings(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> QuestSettingsResponse:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)
        quest = workspace.longFormQuestDef

        if quest is None:
            return QuestSettingsResponse(
                workspace_id=workspace_id,
                type=QuestDefinitionTypeName.NONE,
                definition=None,
                url=None,
                modified_at=workspace.createdAt,
                modified_by=workspace.createdBy,
                modified_by_name="",
            )

        return QuestSettingsResponse(
            workspace_id=quest.workspace_id,
            type=QuestDefinitionTypeName(quest.type.name),
            definition=quest.definition,
            url=quest.url,
            modified_at=quest.modifiedAt,
            modified_by=quest.modifiedBy,
            modified_by_name=quest.modifiedByName,
        )
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly validates the long quest definition against the JSON schema and returns a 400 if the definition is invalid
# @test: Test that this endpoint properly saves the long quest definition to the database and returns a 204 on success
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles input values that are properly formed, malformed and edge cases, including empty lists, null values, and large payloads
# @test: Test that this method properly calls the repository method to save the long quest definition and that the repository method properly saves the definition to the database


# Returns 204 on success
@router.patch(
    "/{workspace_id}/quests/long/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_long_quest_settings(
    workspace_id: int,
    long_quest_data: QuestSettingsPatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )

    if long_quest_data.type == QuestDefinitionTypeName.JSON:
        if long_quest_data.definition is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'definition' is required for JSON quest type.",
            )
        await validate_quest_definition_schema(long_quest_data.definition)

    try:
        await repository_ws.save_longform_quest(
            current_user, workspace_id, long_quest_data
        )
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


# IMAGERY

# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this method properly calls the repository method to fetch the imagery definition


# Returns JSON payload or 204 if not set
@router.get("/{workspace_id}/imagery/settings")
async def get_imagery_settings(
    workspace_id: int,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> WorkspaceImagery | None:
    try:
        workspace = await repository_ws.getById(current_user, workspace_id)

        if workspace.imageryListDef is None:
            return WorkspaceImagery(
                workspace_id=workspace_id,
                definition=[],
                modifiedAt=workspace.createdAt,
                modifiedBy=workspace.createdBy,
                modifiedByName="",
            )
        else:
            return workspace.imageryListDef
    except Exception as e:
        logger.error(f"Failed to fetch workspace {workspace_id}: {str(e)}")
        raise


# @test: Test that this endpoint properly validates the user's permissions and returns a 403 if the user is not a workspace lead
# @test: Test that this endpoint properly validates the imagery definition against the JSON schema and returns a 400 if the definition is invalid
# @test: Test that this endpoint properly saves the imagery definition to the database and returns a 204 on success
# @test: Test that this endpoint properly handles any exceptions and returns a 500 if an unexpected error occurs
# @test: Test that this endpoint properly handles the case where the workspace does not exist and returns a 404
# @test: Test that this endpoint properly handles input values that are properly formed, malformed and edge cases, including empty lists, null values, and large payloads
# @test: Test that this method properly calls the repository method to save the imagery definition and that the repository method properly saves the definition to the database


# Returns 204 on success
@router.patch(
    "/{workspace_id}/imagery/settings", status_code=status.HTTP_204_NO_CONTENT
)
async def update_imagery_settings(
    workspace_id: int,
    imagery_data: ImagerySettingsPatch,
    repository_ws: WorkspaceRepository = Depends(get_workspace_repository),
    current_user: UserInfo = Depends(validate_token),
) -> None:
    if not current_user.isWorkspaceLead(workspace_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not have permission to edit this workspace",
        )

    if imagery_data.definition:
        await validate_imagery_definition_schema(imagery_data.definition)

    try:
        await repository_ws.save_imagery_def(current_user, workspace_id, imagery_data)
    except Exception as e:
        logger.error(f"Failed to update workspace {workspace_id}: {str(e)}")
        raise


@router.get("/{workspace_id}/jobs", response_model=list[Job])
async def get_jobs_by_workspace_id(
    workspace_id: int,
    repository: JobRepository = Depends(get_jobs_repository),
    current_user: UserInfo = Depends(validate_token),
) -> list[Job]:
    try:
        jobs = await repository.getWorkspaceJobs(current_user, workspace_id)
        return jobs
    except Exception as e:
        logger.error(f"Failed to fetch jobs for workspace {workspace_id}: {str(e)}")
        raise
