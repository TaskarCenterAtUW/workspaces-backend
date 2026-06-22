from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import shape as shapely_shape
from sqlalchemy import func, text, or_, select, update
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
    ProjectRoleAddRequest,
    ProjectRoleItem,
    ProjectRoleListResponse,
    ProjectRoleUpdateRequest,
    ProjectUpdateRequest,
    SelfProjectRoleItem,
    SelfProjectRolesResponse,
)
from api.src.tasking.audit.schemas import AuditEventType
from api.src.tasking.projects.schemas import (
    AoiInput,
    ProjectRoleType,
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

    async def _audit(
        self,
        *,
        event_type: AuditEventType,
        project_id: int,
        actor_uuid: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO tasking_audit_events "
                "(event_type, project_id, task_id, actor_user_auth_uid, details) "
                "VALUES (:et, :pid, NULL, :uid, CAST(:dt AS jsonb))"
            ),
            {
                "et": event_type,
                "pid": project_id,
                "uid": str(actor_uuid),
                "dt": json.dumps(details or {}),
            },
        )

    # ---- internal helpers --------------------------------------------

    async def _get_active(self, workspace_id: int, project_id: int) -> TaskingProject:
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

    async def _provision_users_from_tdei(
        self,
        missing_uuids: list[str],
        project_group_id: str,
        bearer_token: str,
    ) -> list[str]:
        """Look up `missing_uuids` against TDEI's project-group members
        and insert matching rows into `users`. Return the UUIDs that are
        still unknown (not members of the project group at all).

        This is the auto-provisioning fallback for `role_assignments[]`:
        a user who is known to TDEI but has not yet performed any action
        that would write them into `users` (e.g. first-time sign-in).
        """
        from sqlalchemy import text

        from api.core.security import fetch_project_group_users

        try:
            members = await fetch_project_group_users(project_group_id, bearer_token)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to fetch project group users from TDEI: {e}",
            ) from e

        by_uid = {m.auth_uid: m for m in members}

        resolved: set[str] = set()
        for uid in missing_uuids:
            member = by_uid.get(uid)
            if member is None:
                continue
            await self.session.execute(
                text(
                    "INSERT INTO users (auth_uid, email, display_name) "
                    "VALUES (:uid, :email, :name) "
                    "ON CONFLICT (auth_uid) DO NOTHING"
                ),
                {
                    "uid": uid,
                    "email": member.email,
                    "name": member.display_name,
                },
            )
            resolved.add(uid)

        if resolved:
            # Commit the new `users` rows in their own transaction so the
            # subsequent FK insert into `tasking_project_roles` resolves.
            await self.session.commit()

        return [u for u in missing_uuids if u not in resolved]

    async def _missing_user_auth_uids(self, uuids: list[UUID]) -> list[str]:
        """Return the subset of `uuids` without a matching `users` row.

        Preflight for the `tasking_project_roles.user_auth_uid` FK so
        downstream inserts produce a clean 422 with the offending ids
        instead of a 23503 foreign-key-violation.
        """
        if not uuids:
            return []

        from sqlalchemy import text

        rows = await self.session.execute(
            text("SELECT auth_uid FROM users WHERE auth_uid = ANY(:uids)"),
            {"uids": [str(u) for u in uuids]},
        )
        existing = {row[0] for row in rows.all()}
        return [str(u) for u in uuids if str(u) not in existing]

    async def _task_count(self, project_id: int) -> int:
        """Read-only task count for a project; raw SQL to keep this
        module independent of the tasks sub-module's ORM."""
        from sqlalchemy import text

        result = await self.session.execute(
            text("SELECT COUNT(*) FROM tasking_tasks WHERE project_id = :pid"),
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
        tdei_project_group_id: str,
    ) -> ProjectResponse:
        # Preflight every user_auth_uid that will be inserted into
        # `tasking_project_roles` — the creator's auto-LEAD seed plus
        # any explicit role_assignments. Any UUID missing from `users`
        # is looked up against TDEI's project-group member list and
        # auto-provisioned; only UUIDs that are not members at all
        # surface as a 422 to the caller.
        candidate_uuids: list[UUID] = [current_user.user_uuid]
        candidate_uuids.extend(ra.user_id for ra in body.role_assignments or [])
        missing = await self._missing_user_auth_uids(candidate_uuids)

        if missing:
            creator_uid = str(current_user.user_uuid)
            if creator_uid in missing:
                # The signed-in caller is not in `users`. Pull them
                # from TDEI just like any other role-assignment user;
                # if even TDEI doesn't know them, surface 409 because
                # the request can never succeed.
                pass

            # Auto-provision via TDEI for the members of this project group.
            still_missing = await self._provision_users_from_tdei(
                missing,
                project_group_id=tdei_project_group_id,
                bearer_token=current_user.credentials,
            )

            if creator_uid in still_missing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Your user record could not be provisioned: TDEI "
                        "does not list you as a member of this project "
                        "group. Sign in to Workspaces once, then retry."
                    ),
                )
            if still_missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "message": (
                            "One or more `role_assignments[].user_id` "
                            "values are not members of this workspace's "
                            "project group in TDEI."
                        ),
                        "missing_user_ids": still_missing,
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

            await self._audit(
                event_type=AuditEventType.PROJECT_CREATED,
                project_id=project.id,  # type: ignore[arg-type]
                actor_uuid=current_user.user_uuid,
                details={"name": project.name},
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
        current_user: UserInfo,
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
                await self._audit(
                    event_type=AuditEventType.PROJECT_EDITED,
                    project_id=project.id,  # type: ignore[arg-type]
                    actor_uuid=current_user.user_uuid,
                    details={"updatedFields": [k for k in updates if k != "updated_at"]},
                )
                await self.session.commit()
            except IntegrityError as e:
                await self.session.rollback()
                raise _translate_integrity_error(e) from e
            await self.session.refresh(project)

        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    async def soft_delete(self, workspace_id: int, project_id: int, current_user: UserInfo) -> None:
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
        # Insert the deletion event before flagging so this row is caught too.
        await self._audit(
            event_type=AuditEventType.PROJECT_DELETED,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
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

    async def activate(self, workspace_id: int, project_id: int, current_user: UserInfo) -> ProjectResponse:
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
        await self._audit(
            event_type=AuditEventType.PROJECT_ACTIVATED,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
        )
        await self.session.commit()
        await self.session.refresh(project)
        return self._to_response(project, task_count=tc)

    async def close(self, workspace_id: int, project_id: int, current_user: UserInfo) -> ProjectResponse:
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
        await self._audit(
            event_type=AuditEventType.PROJECT_CLOSED,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
        )
        await self.session.commit()
        await self.session.refresh(project)
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    async def reset(self, workspace_id: int, project_id: int, current_user: UserInfo) -> ProjectResponse:
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

        await self._audit(
            event_type=AuditEventType.PROJECT_RESET,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
        )
        await self.session.commit()
        await self.session.refresh(project)
        tc = await self._task_count(project.id)  # type: ignore[arg-type]
        return self._to_response(project, task_count=tc)

    # ---- AOI ---------------------------------------------------------

    async def get_aoi(self, workspace_id: int, project_id: int) -> AoiFeature:
        project = await self._get_active(workspace_id, project_id)
        if project.aoi is None:
            raise NotFoundException("AOI is not set on this project")
        geom = to_shape(project.aoi)
        if isinstance(geom, ShapelyPolygon):  # defensive
            geom = ShapelyMultiPolygon([geom])
        return _shapely_to_aoi_feature(geom)

    async def upload_aoi(
        self, workspace_id: int, project_id: int, aoi: AoiInput, current_user: UserInfo
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
        await self._audit(
            event_type=AuditEventType.AOI_UPLOADED,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
        )
        await self.session.commit()
        return _shapely_to_aoi_feature(geom)

    # ---- Project roles ------------------------------------------------
    #
    # Schema lives in `tasking_project_roles (project_id, user_auth_uid,
    # role, updated_at)` with PK on (project_id, user_auth_uid) and a FK
    # to `users.auth_uid`. The `add` path inserts via raw SQL so duplicate
    # rows raise the PK uniqueness violation translated into a 409; FK
    # violations on `user_auth_uid` are caught with a preflight so the
    # caller gets a 422 listing the missing user id.

    async def _is_project_lead(self, project_id: int, user_uuid: UUID) -> bool:
        """True if the user holds a `lead` role on the given project."""
        from sqlalchemy import text

        result = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_project_roles "
                "WHERE project_id = :pid AND user_auth_uid = :uid "
                "AND role = 'lead' LIMIT 1"
            ),
            {"pid": project_id, "uid": str(user_uuid)},
        )
        return result.scalar() is not None

    async def assert_can_manage_roles(
        self,
        workspace_id: int,
        project_id: int,
        current_user: UserInfo,
    ) -> None:
        """Permission gate for role-management endpoints.

        Allows either a workspace-level LEAD (per `UserInfo.isWorkspaceLead`)
        or a project-level LEAD recorded in `tasking_project_roles`. The
        project must already exist; otherwise the underlying `_get_active`
        call raises 404.
        """
        await self._get_active(workspace_id, project_id)
        if current_user.isWorkspaceLead(workspace_id):
            return
        if await self._is_project_lead(project_id, current_user.user_uuid):
            return
        raise ForbiddenException(
            "Only a workspace lead or project lead can manage roles " "on this project."
        )

    async def _lead_count(self, project_id: int) -> int:
        from sqlalchemy import text

        result = await self.session.execute(
            text(
                "SELECT COUNT(*) FROM tasking_project_roles "
                "WHERE project_id = :pid AND role = 'lead'"
            ),
            {"pid": project_id},
        )
        return int(result.scalar() or 0)

    async def list_roles(
        self, workspace_id: int, project_id: int
    ) -> ProjectRoleListResponse:
        """Return every role assignment on the project, newest first."""
        await self._get_active(workspace_id, project_id)
        from sqlalchemy import text

        rows = await self.session.execute(
            text(
                "SELECT r.user_auth_uid, u.display_name, r.role, r.updated_at "
                "FROM tasking_project_roles r "
                "LEFT JOIN users u ON u.auth_uid = r.user_auth_uid "
                "WHERE r.project_id = :pid "
                "ORDER BY r.updated_at DESC"
            ),
            {"pid": project_id},
        )
        items = [
            ProjectRoleItem(
                user_id=UUID(uid),
                user_name=name,
                role=ProjectRoleType(role),
                updated_at=updated,
            )
            for uid, name, role, updated in rows.all()
        ]
        return ProjectRoleListResponse(results=items)

    async def add_role(
        self,
        workspace_id: int,
        project_id: int,
        body: ProjectRoleAddRequest,
    ) -> ProjectRoleItem:
        """Insert a new role row. 422 on missing user; 409 on duplicate."""
        await self._get_active(workspace_id, project_id)

        missing = await self._missing_user_auth_uids([body.user_id])
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "`user_id` refers to a user that has not signed in "
                        "to Workspaces yet — no `users` row exists."
                    ),
                    "missing_user_ids": missing,
                },
            )

        from sqlalchemy import text

        existing = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_project_roles "
                "WHERE project_id = :pid AND user_auth_uid = :uid"
            ),
            {"pid": project_id, "uid": str(body.user_id)},
        )
        if existing.scalar() is not None:
            raise AlreadyExistsException(
                "This user is already assigned a role on the project. "
                "Use PATCH to change their role."
            )

        try:
            await self.session.execute(
                text(
                    "INSERT INTO tasking_project_roles "
                    "(project_id, user_auth_uid, role, updated_at) "
                    "VALUES (:pid, :uid, :role, NOW())"
                ),
                {
                    "pid": project_id,
                    "uid": str(body.user_id),
                    "role": body.role.value,
                },
            )
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise _translate_integrity_error(e) from e

        # Re-read so the response carries server-set `updated_at` and the
        # `display_name` join with `users`.
        return await self._get_role(project_id, body.user_id)

    async def update_role(
        self,
        workspace_id: int,
        project_id: int,
        user_id: UUID,
        body: ProjectRoleUpdateRequest,
    ) -> ProjectRoleItem:
        """Change a user's role. 404 if not assigned. 422 if it would
        remove the last LEAD (per spec — projects must always have one)."""
        await self._get_active(workspace_id, project_id)

        current = await self._get_role_or_none(project_id, user_id)
        if current is None:
            raise NotFoundException(
                f"User {user_id} has no role on project {project_id}"
            )

        # Guard: demoting the only LEAD would orphan the project.
        if (
            current.role == ProjectRoleType.LEAD
            and body.role != ProjectRoleType.LEAD
            and await self._lead_count(project_id) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Cannot demote the last LEAD on this project. Assign "
                    "another LEAD first."
                ),
            )

        from sqlalchemy import text

        await self.session.execute(
            text(
                "UPDATE tasking_project_roles "
                "SET role = :role, updated_at = NOW() "
                "WHERE project_id = :pid AND user_auth_uid = :uid"
            ),
            {
                "pid": project_id,
                "uid": str(user_id),
                "role": body.role.value,
            },
        )
        await self.session.commit()
        return await self._get_role(project_id, user_id)

    async def list_self_project_roles(
        self,
        workspace_id: int,
        current_user: UserInfo,
    ) -> SelfProjectRolesResponse:
        """One row per non-deleted project in the workspace with the
        caller's effective role on that project.

        Resolution rule:
          1. If `tasking_project_roles` has an explicit row for this
             user on the project, that wins.
          2. Otherwise the caller's workspace-level role applies (the
             implicit fallback that grants `contributor` access to
             every project in the workspace).

        Workspace-level role is mapped to the same `ProjectRoleType`
        enum the project rows use.
        """
        # Workspace-level role first — used as the fallback for projects
        # without an explicit override.
        from api.src.users.schemas import WorkspaceUserRoleType

        ws_role_raw = current_user.effective_role(workspace_id)
        ws_role = ProjectRoleType(ws_role_raw.value)

        from sqlalchemy import text

        rows = await self.session.execute(
            text(
                "SELECT p.id, p.name, p.status, r.role "
                "FROM tasking_projects p "
                "LEFT JOIN tasking_project_roles r "
                "  ON r.project_id = p.id "
                " AND r.user_auth_uid = :uid "
                "WHERE p.workspace_id = :wid "
                "  AND p.deleted_at IS NULL "
                "ORDER BY p.created_at DESC"
            ),
            {
                "wid": workspace_id,
                "uid": str(current_user.user_uuid),
            },
        )

        items = [
            SelfProjectRoleItem(
                project_id=pid,
                project_name=name,
                project_status=ProjectStatus(status_value),
                role=(
                    ProjectRoleType(explicit_role)
                    if explicit_role is not None
                    else ws_role
                ),
            )
            for pid, name, status_value, explicit_role in rows.all()
        ]
        return SelfProjectRolesResponse(
            workspace_role=ws_role,
            projects=items,
        )

    async def get_role(
        self,
        workspace_id: int,
        project_id: int,
        user_id: UUID,
    ) -> ProjectRoleItem:
        """Single-user role read. 404 if no assignment exists."""
        await self._get_active(workspace_id, project_id)
        item = await self._get_role_or_none(project_id, user_id)
        if item is None:
            raise NotFoundException(
                f"User {user_id} has no role on project {project_id}"
            )
        return item

    async def upsert_role(
        self,
        workspace_id: int,
        project_id: int,
        user_id: UUID,
        body: ProjectRoleUpdateRequest,
    ) -> tuple[ProjectRoleItem, bool]:
        """PUT semantics: idempotent insert-or-update on a role row.

        Returns ``(item, created)`` where ``created`` is True iff the
        row did not exist before this call. The route layer uses that
        flag to pick 201 vs 200.

        Honours the last-LEAD guard: a PUT that would demote the only
        remaining LEAD returns 422 — assign another LEAD first.
        """
        await self._get_active(workspace_id, project_id)

        current = await self._get_role_or_none(project_id, user_id)

        if current is not None:
            # Update path — guard against orphaning the project.
            if (
                current.role == ProjectRoleType.LEAD
                and body.role != ProjectRoleType.LEAD
                and await self._lead_count(project_id) <= 1
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "Cannot demote the last LEAD on this project. "
                        "Assign another LEAD first."
                    ),
                )
        else:
            # Insert path — `user_id` must exist in `users`. Preflight so
            # the caller gets a 422 with `missing_user_ids` rather than
            # a generic 23503 FK violation.
            missing = await self._missing_user_auth_uids([user_id])
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "message": (
                            "`user_id` refers to a user that has not signed "
                            "in to Workspaces yet — no `users` row exists."
                        ),
                        "missing_user_ids": missing,
                    },
                )

        from sqlalchemy import text

        try:
            await self.session.execute(
                text(
                    "INSERT INTO tasking_project_roles "
                    "(project_id, user_auth_uid, role, updated_at) "
                    "VALUES (:pid, :uid, :role, NOW()) "
                    "ON CONFLICT (project_id, user_auth_uid) DO UPDATE "
                    "SET role = EXCLUDED.role, updated_at = NOW()"
                ),
                {
                    "pid": project_id,
                    "uid": str(user_id),
                    "role": body.role.value,
                },
            )
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            raise _translate_integrity_error(e) from e

        item = await self._get_role(project_id, user_id)
        return item, current is None

    async def remove_role(
        self,
        workspace_id: int,
        project_id: int,
        user_id: UUID,
    ) -> None:
        """Drop a role assignment. 404 if absent. 422 on last-LEAD removal."""
        await self._get_active(workspace_id, project_id)

        current = await self._get_role_or_none(project_id, user_id)
        if current is None:
            raise NotFoundException(
                f"User {user_id} has no role on project {project_id}"
            )

        if (
            current.role == ProjectRoleType.LEAD
            and await self._lead_count(project_id) <= 1
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Cannot remove the last LEAD on this project. Assign "
                    "another LEAD first."
                ),
            )

        from sqlalchemy import text

        await self.session.execute(
            text(
                "DELETE FROM tasking_project_roles "
                "WHERE project_id = :pid AND user_auth_uid = :uid"
            ),
            {"pid": project_id, "uid": str(user_id)},
        )
        await self.session.commit()

    async def _get_role(self, project_id: int, user_id: UUID) -> ProjectRoleItem:
        item = await self._get_role_or_none(project_id, user_id)
        if item is None:  # pragma: no cover — only called after insert/update
            raise NotFoundException(
                f"User {user_id} has no role on project {project_id}"
            )
        return item

    async def _get_role_or_none(
        self, project_id: int, user_id: UUID
    ) -> ProjectRoleItem | None:
        from sqlalchemy import text

        row = await self.session.execute(
            text(
                "SELECT r.user_auth_uid, u.display_name, r.role, r.updated_at "
                "FROM tasking_project_roles r "
                "LEFT JOIN users u ON u.auth_uid = r.user_auth_uid "
                "WHERE r.project_id = :pid AND r.user_auth_uid = :uid"
            ),
            {"pid": project_id, "uid": str(user_id)},
        )
        result = row.first()
        if result is None:
            return None
        uid, name, role, updated = result
        return ProjectRoleItem(
            user_id=UUID(uid),
            user_name=name,
            role=ProjectRoleType(role),
            updated_at=updated,
        )

    async def delete_aoi(self, workspace_id: int, project_id: int, current_user: UserInfo) -> None:
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
        await self._audit(
            event_type=AuditEventType.AOI_DELETED,
            project_id=project.id,  # type: ignore[arg-type]
            actor_uuid=current_user.user_uuid,
        )
        await self.session.commit()


__all__ = ["TaskingProjectRepository"]
