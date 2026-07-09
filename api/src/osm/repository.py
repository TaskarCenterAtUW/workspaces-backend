from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import NotFoundException


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

        sql_query = text("select MAX(latitude) AS max_lat, MAX(longitude) AS max_lon, \
                    MIN(latitude) AS min_lat, MIN(longitude) AS min_lon from nodes")

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
        workspace_id: int,
        changeset_id: int,
        reviewer_uuid: str,
    ) -> None:
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
