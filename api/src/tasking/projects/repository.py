from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import shape as shapely_shape
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import (
    AlreadyExistsException,
    ForbiddenException,
    NotFoundException,
)
from api.core.security import UserInfo
from api.src.tasking.projects.dtos import (
    AoiFeature,
    Pagination,
    ProjectCreateRequest,
    ProjectListItem,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
)
from api.src.tasking.projects.schemas import (
    AoiInput,
    ProjectStatus,
    TaskingProject,
    _Feature,
    _FeatureCollection,
    _MultiPolygon,
    _Polygon,
)


# ---------------------------------------------------------------------------
# AOI helpers
# ---------------------------------------------------------------------------


def _aoi_to_shapely(aoi: AoiInput) -> ShapelyMultiPolygon:
    """Normalise any of the accepted GeoJSON shapes to a Shapely
    MultiPolygon. Bare Polygons are upcast to a single-member
    MultiPolygon — storage column is always MULTIPOLYGON(4326).
    """
    if isinstance(aoi, _FeatureCollection):
        geom_dict = aoi.features[0].geometry.model_dump()
    elif isinstance(aoi, _Feature):
        geom_dict = aoi.geometry.model_dump()
    elif isinstance(aoi, (_Polygon, _MultiPolygon)):
        geom_dict = aoi.model_dump()
    else:  # pragma: no cover — Pydantic guards against this
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported AOI shape",
        )

    try:
        geom = shapely_shape(geom_dict)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid AOI geometry: {e}",
        ) from None

    if isinstance(geom, ShapelyPolygon):
        geom = ShapelyMultiPolygon([geom])
    elif not isinstance(geom, ShapelyMultiPolygon):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="AOI must be a Polygon or MultiPolygon",
        )

    if not geom.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"AOI is not a valid polygon: {geom.is_valid_reason if hasattr(geom, 'is_valid_reason') else 'self-intersection or invalid ring'}",
        )

    return geom


def _shapely_to_aoi_feature(geom: ShapelyMultiPolygon) -> AoiFeature:
    """Build the GeoJSON Feature wrapper returned by the AOI GET endpoint."""
    raw = geom.__geo_interface__
    return AoiFeature(
        type="Feature",
        geometry=_MultiPolygon(
            type="MultiPolygon",
            coordinates=raw["coordinates"],
        ),
        properties={},
    )


# ---------------------------------------------------------------------------
# Constraint translation — map Postgres `IntegrityError` to a precise
# HTTPException keyed by constraint name. Avoids the generic
# "everything is 409: name already exists" message.
# ---------------------------------------------------------------------------


def _constraint_name(e: IntegrityError) -> str | None:
    """Return the PG constraint name from an `IntegrityError`, or None."""
    orig = getattr(e, "orig", None)
    name = getattr(orig, "constraint_name", None)
    if name:
        return name
    inner = getattr(orig, "__cause__", None)
    return getattr(inner, "constraint_name", None)


def _translate_integrity_error(e: IntegrityError) -> HTTPException:
    """Convert a Postgres constraint violation into an HTTPException."""
    name = _constraint_name(e) or ""

    if name == "tasking_projects_workspace_name_unique":
        return AlreadyExistsException(
            "A project with this name already exists in the workspace"
        )

    if name == "tasking_project_roles_user_auth_uid_fkey":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "One or more `role_assignments[].user_id` values refer "
                "to a user that has not signed in to Workspaces yet."
            ),
        )

    if name == "tasking_tasks_project_id_fkey":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot insert tasks: parent project does not exist.",
        )

    # NOT NULL violations surface with no constraint_name on asyncpg.
    orig_class = type(getattr(e, "orig", None)).__name__
    if orig_class == "NotNullViolationError":
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Required field is missing.",
        )

    if name:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Database constraint violated: {name}",
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Database constraint violated.",
    )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TaskingProjectRepository:
    """CRUD and lifecycle for tasking projects.

    Methods assume the caller has already passed the workspace tenancy
    gate (`WorkspaceRepository.getById`); that check is performed at
    the route layer.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- internal helpers --------------------------------------------

    async def _get_active(
        self, workspace_id: int, project_id: int
    ) -> TaskingProject:
        """Fetch a non-deleted project scoped to a workspace; raise 404 otherwise."""
        result = await self.session.execute(
            select(TaskingProject).where(
                (TaskingProject.id == project_id)
                & (TaskingProject.workspace_id == workspace_id)
                & (TaskingProject.deleted_at.is_(None))
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise NotFoundException(f"Project {project_id} not found")
        return project

    @staticmethod
    def _to_response(project: TaskingProject, task_count: int = 0) -> ProjectResponse:
        return ProjectResponse(
            id=project.id,  # type: ignore[arg-type]
            workspace_id=project.workspace_id,
            name=project.name,
            instructions=project.instructions,
            status=project.status,
            review_required=project.review_required,
            lock_timeout_hours=project.lock_timeout_hours,
            task_boundary_type=project.task_boundary_type,
            has_aoi=project.aoi is not None,
            task_count=task_count,
            created_by=project.created_by,
            created_by_name=project.created_by_name,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    async def _missing_user_auth_uids(
        self, uuids: list[UUID]
    ) -> list[str]:
        """Return the subset of `uuids` without a matching `users` row.

        Preflight for the `tasking_project_roles.user_auth_uid` FK so
        downstream inserts produce a clean 422 with the offending ids
        instead of a 23503 foreign-key-violation.
        """
        if not uuids:
            return []

        from sqlalchemy import text

        rows = await self.session.execute(
            text(
                "SELECT auth_uid FROM users WHERE auth_uid = ANY(:uids)"
            ),
            {"uids": [str(u) for u in uuids]},
        )
        existing = {row[0] for row in rows.all()}
        return [str(u) for u in uuids if str(u) not in existing]

    async def _task_count(self, project_id: int) -> int:
        """Read-only task count for a project; raw SQL to keep this
        module independent of the tasks sub-module's ORM."""
        from sqlalchemy import text

        result = await self.session.execute(
            text(
                "SELECT COUNT(*) FROM tasking_tasks WHERE project_id = :pid"
            ),
            {"pid": project_id},
        )
        return int(result.scalar() or 0)

    # ---- create / list / get / patch / delete ------------------------

    async def list_projects(
        self,
        workspace_id: int,
        *,
        status_filter: ProjectStatus | None = None,
        text_search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        order_dir: str = "DESC",
    ) -> ProjectListResponse:
        valid_order = {
            "created_at": TaskingProject.created_at,
            "updated_at": TaskingProject.updated_at,
            "name": TaskingProject.name,
        }
        col = valid_order.get(order_by, TaskingProject.created_at)
        col = col.desc() if order_dir.upper() == "DESC" else col.asc()

        where = (TaskingProject.workspace_id == workspace_id) & (
            TaskingProject.deleted_at.is_(None)
        )
        if status_filter is not None:
            where = where & (TaskingProject.status == status_filter)
        if text_search:
            where = where & (
                func.lower(TaskingProject.name).contains(text_search.lower())
            )

        total_q = await self.session.execute(
            select(func.count()).select_from(TaskingProject).where(where)
        )
        total = int(total_q.scalar() or 0)

        page = max(page, 1)
        page_size = max(min(page_size, 200), 1)
        offset = (page - 1) * page_size

        rows = await self.session.execute(
            select(TaskingProject)
            .where(where)
            .order_by(col)
            .limit(page_size)
            .offset(offset)
        )
        projects = list(rows.scalars().all())

        # task counts in one round trip
        counts: dict[int, int] = {}
        if projects:
            from sqlalchemy import text

            ids = [p.id for p in projects]
            cnt_rows = await self.session.execute(
                text(
                    "SELECT project_id, COUNT(*) FROM tasking_tasks "
                    "WHERE project_id = ANY(:ids) GROUP BY project_id"
                ),
                {"ids": ids},
            )
            counts = {pid: int(c) for pid, c in cnt_rows.all()}

        items: list[ProjectListItem] = []
        for p in projects:
            tc = counts.get(p.id, 0)  # type: ignore[arg-type]
            completed = 0
            if tc > 0:
                from sqlalchemy import text

                done_q = await self.session.execute(
                    text(
                        "SELECT COUNT(*) FROM tasking_tasks "
                        "WHERE project_id = :pid AND status = 'completed'"
                    ),
                    {"pid": p.id},
                )
                completed = int(done_q.scalar() or 0)
            pct = int(round((completed / tc) * 100)) if tc > 0 else 0
            items.append(
                ProjectListItem(
                    id=p.id,  # type: ignore[arg-type]
                    name=p.name,
                    status=p.status,
                    task_count=tc,
                    percent_completed=pct,
                    created_by=p.created_by,
                    created_by_name=p.created_by_name,
                    created_at=p.created_at,
                    updated_at=p.updated_at,
                )
            )

        return ProjectListResponse(
            results=items,
            pagination=Pagination(page=page, page_size=page_size, total=total),
        )

    async def create(
        self,
        workspace_id: int,
        current_user: UserInfo,
        body: ProjectCreateRequest,
    ) -> ProjectResponse:
        # Preflight every user_auth_uid that will be inserted into
        # `tasking_project_roles` — the creator's auto-LEAD seed plus
        # any explicit role_assignments. Returns a 422 listing the
        # missing ids instead of a generic FK violation.
        candidate_uuids: list[UUID] = [current_user.user_uuid]
        candidate_uuids.extend(ra.user_id for ra in body.role_assignments or [])
        missing = await self._missing_user_auth_uids(candidate_uuids)
        if missing:
            creator_uid = str(current_user.user_uuid)
            if creator_uid in missing:
                # Signed-in caller is not yet provisioned in `users`;
                # distinct from a bad role_assignments entry.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Your user record has not been provisioned yet. "
                        "Sign in to Workspaces once to create your `users` "
                        "row, then retry."
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "One or more `role_assignments[].user_id` values "
                        "refer to a user that has not signed in to "
                        "Workspaces yet — no `users` row exists."
                    ),
                    "missing_user_ids": missing,
                },
            )

        project = TaskingProject(
            workspace_id=workspace_id,
            name=body.name,
            instructions=body.instructions,
            review_required=body.review_required,
            lock_timeout_hours=body.lock_timeout_hours,
            created_by=current_user.user_uuid,
            created_by_name=current_user.user_name,
        )
        if body.aoi is not None:
            geom = _aoi_to_shapely(body.aoi)
            project.aoi = from_shape(geom, srid=4326)

        try:
            self.session.add(project)
            await self.session.flush()  # need project.id for role rows

            # Seed project-level role overrides.
            if body.role_assignments:
                from sqlalchemy import text

                for ra in body.role_assignments:
                    await self.session.execute(
                        text(
                            "INSERT INTO tasking_project_roles "
                            "(project_id, user_auth_uid, role) "
                            "VALUES (:pid, :uid, :role) "
                            "ON CONFLICT (project_id, user_auth_uid) "
                            "DO UPDATE SET role = EXCLUDED.role, "
                            "              updated_at = NOW()"
                        ),
                        {
                            "pid": project.id,
                            "uid": str(ra.user_id),
                            "role": ra.role,
                        },
                    )

            # Creator is auto-assigned the LEAD role on the project,
            # mirroring the workspace-creator auto-LEAD convention.
            from sqlalchemy import text

            await self.session.execute(
                text(
                    "INSERT INTO tasking_project_roles "
                    "(project_id, user_auth_uid, role) "
                    "VALUES (:pid, :uid, 'lead') "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "pid": project.id,
                    "uid": str(current_user.user_uuid),
                },
            )

            await self.session.commit()
            await self.session.refresh(project)
        except IntegrityError as e:
            await self.session.rollback()
            raise _translate_integrity_error(e) from e

        return self._to_response(project)

    async def get(self, workspace_id: int, project_id: int) -> ProjectResponse:
        project = await self._get_active(workspace_id, project_id)
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    async def patch(
        self,
        workspace_id: int,
        project_id: int,
        body: ProjectUpdateRequest,
    ) -> ProjectResponse:
        project = await self._get_active(workspace_id, project_id)

        if project.status == ProjectStatus.DONE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A closed project cannot be edited",
            )

        # `review_required` immutable after activation
        if (
            body.review_required is not None
            and project.status != ProjectStatus.DRAFT
            and body.review_required != project.review_required
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="review_required is immutable after activation",
            )

        updates: dict[str, Any] = {}
        if body.name is not None:
            updates["name"] = body.name.strip()
        if body.instructions is not None:
            updates["instructions"] = body.instructions
        if body.lock_timeout_hours is not None:
            updates["lock_timeout_hours"] = body.lock_timeout_hours
        if body.review_required is not None:
            updates["review_required"] = body.review_required

        if updates:
            updates["updated_at"] = datetime.now()
            try:
                await self.session.execute(
                    update(TaskingProject)
                    .where(TaskingProject.id == project.id)
                    .values(**updates)
                )
                await self.session.commit()
            except IntegrityError as e:
                await self.session.rollback()
                raise _translate_integrity_error(e) from e
            await self.session.refresh(project)

        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    async def soft_delete(self, workspace_id: int, project_id: int) -> None:
        project = await self._get_active(workspace_id, project_id)

        # Refuse if any active task locks remain.
        from sqlalchemy import text

        active = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_locks "
                "WHERE project_id = :pid AND released_at IS NULL LIMIT 1"
            ),
            {"pid": project.id},
        )
        if active.scalar() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project has active task locks; force-release first",
            )

        # Soft-delete the project, hard-delete its tasks, flag audit rows.
        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(deleted_at=datetime.now())
        )
        await self.session.execute(
            text("DELETE FROM tasking_tasks WHERE project_id = :pid"),
            {"pid": project.id},
        )
        await self.session.execute(
            text(
                "UPDATE tasking_audit_events SET project_deleted = TRUE "
                "WHERE project_id = :pid"
            ),
            {"pid": project.id},
        )
        await self.session.commit()

    # ---- lifecycle transitions ---------------------------------------

    async def activate(
        self, workspace_id: int, project_id: int
    ) -> ProjectResponse:
        project = await self._get_active(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only draft projects can be activated",
            )
        if not project.name.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project name is required",
            )
        if project.aoi is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project AOI is required",
            )
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        if tc == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project must have at least one task",
            )

        # Activation requires at least one explicit contributor or
        # validator allocation (creator's auto-LEAD does not count).
        from sqlalchemy import text

        worker_q = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_project_roles "
                "WHERE project_id = :pid AND role IN ('contributor', 'validator') "
                "LIMIT 1"
            ),
            {"pid": project.id},
        )
        if worker_q.scalar() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one contributor or validator must be allocated to the project",
            )

        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(status=ProjectStatus.OPEN, updated_at=datetime.now())
        )
        await self.session.commit()
        await self.session.refresh(project)
        return self._to_response(project, task_count=tc)

    async def close(
        self, workspace_id: int, project_id: int
    ) -> ProjectResponse:
        project = await self._get_active(workspace_id, project_id)
        if project.status != ProjectStatus.OPEN:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only open projects can be closed",
            )

        from sqlalchemy import text

        not_done = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_tasks "
                "WHERE project_id = :pid AND status <> 'completed' LIMIT 1"
            ),
            {"pid": project.id},
        )
        if not_done.scalar() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project has tasks that are not yet completed",
            )
        active_lock = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_locks "
                "WHERE project_id = :pid AND released_at IS NULL LIMIT 1"
            ),
            {"pid": project.id},
        )
        if active_lock.scalar() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project has active task locks",
            )

        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(status=ProjectStatus.DONE, updated_at=datetime.now())
        )
        await self.session.commit()
        await self.session.refresh(project)
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    async def reset(
        self, workspace_id: int, project_id: int
    ) -> ProjectResponse:
        """LEAD reset — see spec §projects."""
        project = await self._get_active(workspace_id, project_id)
        if project.status == ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot reset a draft project (nothing to reset)",
            )

        from sqlalchemy import text

        # Release every active lock with release_reason='reset'.
        await self.session.execute(
            text(
                "UPDATE tasking_locks "
                "SET released_at = NOW(), release_reason = 'reset' "
                "WHERE project_id = :pid AND released_at IS NULL"
            ),
            {"pid": project.id},
        )
        # Wind tasks back to to_map; clear last_mapper_id.
        await self.session.execute(
            text(
                "UPDATE tasking_tasks "
                "SET status = 'to_map', last_mapper_id = NULL, updated_at = NOW() "
                "WHERE project_id = :pid AND status <> 'to_map'"
            ),
            {"pid": project.id},
        )
        # Project reopens if it was done.
        if project.status == ProjectStatus.DONE:
            await self.session.execute(
                update(TaskingProject)
                .where(TaskingProject.id == project.id)
                .values(status=ProjectStatus.OPEN, updated_at=datetime.now())
            )

        await self.session.commit()
        await self.session.refresh(project)
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    # ---- AOI ---------------------------------------------------------

    async def get_aoi(
        self, workspace_id: int, project_id: int
    ) -> AoiFeature:
        project = await self._get_active(workspace_id, project_id)
        if project.aoi is None:
            raise NotFoundException("AOI is not set on this project")
        geom = to_shape(project.aoi)
        if isinstance(geom, ShapelyPolygon):  # defensive
            geom = ShapelyMultiPolygon([geom])
        return _shapely_to_aoi_feature(geom)

    async def upload_aoi(
        self, workspace_id: int, project_id: int, aoi: AoiInput
    ) -> AoiFeature:
        project = await self._get_active(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="AOI can only be set or replaced while the project is in draft",
            )

        geom = _aoi_to_shapely(aoi)
        from sqlalchemy import text

        # Replacing AOI hard-deletes any saved tasks and clears the
        # boundary type (per spec).
        await self.session.execute(
            text("DELETE FROM tasking_tasks WHERE project_id = :pid"),
            {"pid": project.id},
        )
        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(
                aoi=from_shape(geom, srid=4326),
                task_boundary_type=None,
                updated_at=datetime.now(),
            )
        )
        await self.session.commit()
        return _shapely_to_aoi_feature(geom)

    async def delete_aoi(self, workspace_id: int, project_id: int) -> None:
        project = await self._get_active(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="AOI can only be deleted while the project is in draft",
            )
        if project.aoi is None:
            raise NotFoundException("AOI is not set on this project")

        from sqlalchemy import text

        await self.session.execute(
            text("DELETE FROM tasking_tasks WHERE project_id = :pid"),
            {"pid": project.id},
        )
        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(
                aoi=None,
                task_boundary_type=None,
                updated_at=datetime.now(),
            )
        )
        await self.session.commit()


__all__ = ["TaskingProjectRepository"]
