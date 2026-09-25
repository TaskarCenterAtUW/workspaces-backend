from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import ForbiddenException, NotFoundException
from api.core.security import UserInfo

# This service's own tables and enum types in the OSM database's `public`
# schema. Rails creates each workspace schema by cloning `public` (Apartment's
# use_sql pg_dump), so a new workspace schema starts with copies of these too.
# Keep in step with the b7d4e2a9c1f0 migration, which removed the copies made
# before they were dropped at creation.
SERVICE_TABLES = [
    "alembic_version",
    "jobs",
    "teams",
    "team_user",
    "user_workspace_roles",
    "tasking_projects",
    "tasking_tasks",
    "tasking_project_roles",
    "tasking_locks",
    "tasking_changesets",
    "tasking_feedback",
    "tasking_audit_events",
    "tasking_task_save_idempotency",
]
SERVICE_ENUMS = [
    "workspace_role",
    "tasking_project_status",
    "tasking_task_boundary_type",
    "tasking_task_status",
    "tasking_lock_release_reason",
    "tasking_feedback_reason",
]


class OSMRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def getWorkspaceBBox(
        self,
        workspace_id: int,
    ):
        # Postgres does not support parameter binding for `SET search_path`, so
        # workspace_id is interpolated directly. The explicit int() cast guards
        # against SQL injection if this method is ever called from outside of a
        # FastAPI path handler (where the type annotation acts as a safeguard).
        #
        await self.session.execute(
            text(f"SET search_path TO 'workspace-{int(workspace_id)}', public")
        )

        # OSM stores node latitude/longitude as integers scaled by 1e7
        # (100-nanodegree units), so divide by 1e7 to return decimal degrees.
        sql_query = text(
            "select MAX(latitude) / 1e7 AS max_lat, MAX(longitude) / 1e7 AS max_lon, \
                    MIN(latitude) / 1e7 AS min_lat, MIN(longitude) / 1e7 AS min_lon from nodes"
        )

        result = await self.session.execute(sql_query)
        retVal = result.mappings().first()

        if retVal is None:
            raise NotFoundException(f"Workspace with id {workspace_id} not found")

        return retVal

    async def getChangesetAdiff(self, workspace_id: int, changeset_id: int) -> list:
        await self.session.execute(
            text(f"SET search_path TO 'workspace-{int(workspace_id)}', public")
        )
        result = await self.session.execute(
            text("SELECT * FROM osm_augmented_diff(:changeset_id)"),
            {"changeset_id": changeset_id},
        )

        return list(result.mappings().all())

    async def resolveChangeset(
        self,
        current_user: UserInfo,
        workspace_id: int,
        changeset_id: int,
    ) -> None:
        # Defense in depth: resolving a changeset is a validator/lead
        # capability. The route also enforces this, but gate here too so the
        # repository cannot be misused from another call site.
        if not current_user.isWorkspaceLead(
            workspace_id
        ) and not current_user.isWorkspaceValidator(workspace_id):
            raise ForbiddenException(
                "Only workspace leads and validators can resolve changesets"
            )

        reviewer_uuid = str(current_user.user_uuid)

        await self.session.execute(
            text(f"SET search_path TO 'workspace-{int(workspace_id)}', public")
        )

        await self.session.execute(
            text(
                "DELETE FROM changeset_tags"
                " WHERE changeset_id = :cs_id AND k = 'review_requested'"
            ),
            {"cs_id": changeset_id},
        )

        await self.session.execute(
            text(
                "INSERT INTO changeset_tags (changeset_id, k, v)"
                " VALUES (:cs_id, 'reviewed_by', :uid)"
                " ON CONFLICT (changeset_id, k) DO UPDATE SET v = :uid"
            ),
            {"cs_id": changeset_id, "uid": reviewer_uuid},
        )

        await self.session.commit()

    async def dropServiceTableCopies(self, workspace_id: int) -> None:
        """Drop the copies of this service's tables from a new workspace schema.

        The same move Rails makes for `users` right after creating the schema:
        a copy shadows the `public` table for anything whose search_path puts
        the workspace schema first, and nothing is meant to read it.
        """
        schema = f'"workspace-{int(workspace_id)}"'

        for table in SERVICE_TABLES:
            await self.session.execute(
                text(f"DROP TABLE IF EXISTS {schema}.{table} CASCADE")
            )
        for enum in SERVICE_ENUMS:
            await self.session.execute(text(f"DROP TYPE IF EXISTS {schema}.{enum}"))

        await self.session.commit()
