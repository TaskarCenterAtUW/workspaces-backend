from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import shape as shapely_shape
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import (
    AlreadyExistsException,
    ForbiddenException,
    NotFoundException,
)
from api.core.security import UserInfo
from api.src.tasking.projects.dtos import Pagination
from api.src.tasking.projects.schemas import (
    ProjectStatus,
    TaskingProject,
)
from api.src.tasking.tasks.dtos import (
    ExistingLockSummary,
    FeedbackInput,
    LastMapper,
    SaveTasksRequest,
    SaveTasksResponse,
    SubmitRequest,
    TaskBoundariesFeatureCollection,
    TaskBoundaryFeature,
    TaskBoundaryPolygon,
    TaskListResponse,
    TaskLockSummary,
    TaskResponse,
    ValidatePreviewResponse,
    ValidateWarning,
)
from api.src.tasking.tasks.schemas import (
    LockReleaseReason,
    TaskingChangeset,
    TaskingFeedback,
    TaskingLock,
    TaskingTask,
    TaskStatus,
)


# Equirectangular approximation for area calculations on small
# EPSG:4326 polygons: 1 degree latitude ≈ 111.32 km. Sufficient for
# the grid-size warning threshold; precise areas need a metric
# reprojection.
_DEG2_TO_KM2 = 111.32 * 111.32

# Threshold for the `polygon_exceeds_grid_size` warning. Default is
# 5000 m per side (matching the conventional Tasking Manager grid).
# Overridable via the `TM_TASKING_GRID_SIZE_METERS` environment
# variable; read once at import time.
import os as _os  # noqa: E402

try:
    _GRID_SIZE_M = float(_os.getenv("TM_TASKING_GRID_SIZE_METERS", "5000"))
except ValueError:
    _GRID_SIZE_M = 5000.0
_GRID_MAX_KM2 = (_GRID_SIZE_M / 1000.0) ** 2


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _polygon_to_shapely(geom_dict: dict) -> ShapelyPolygon:
    try:
        geom = shapely_shape(geom_dict)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid task geometry: {e}",
        ) from None
    if not isinstance(geom, ShapelyPolygon):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Each task feature must be a Polygon",
        )
    if not geom.is_valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Task polygon is not valid (self-intersection or invalid ring)",
        )
    return geom


def _shapely_polygon_to_geojson(geom: ShapelyPolygon) -> TaskBoundaryPolygon:
    raw = geom.__geo_interface__
    return TaskBoundaryPolygon(type="Polygon", coordinates=raw["coordinates"])


def _polygon_area_km2(geom: ShapelyPolygon) -> float:
    return float(geom.area) * _DEG2_TO_KM2


# AOI containment is checked with `aoi.covers(poly)`, which uses exact
# predicates. Grid generation produces cells clipped against the AOI;
# the clipped vertices land on (or fractionally outside) the AOI edge
# due to floating-point rounding, so `covers` returns False even though
# the cell is topologically inside the AOI.
#
# Treat a polygon as inside the AOI when the area of `poly.difference(aoi)`
# is below this fraction of the polygon's own area. 1e-9 corresponds to
# ~1 µm² at this latitude — well below any real-world AOI authoring error.
_AOI_COVER_REL_TOLERANCE = 1e-9


def _aoi_covers(aoi_geom, poly: ShapelyPolygon) -> bool:
    """Tolerant containment test for AOI vs. task polygon."""
    if aoi_geom.covers(poly):
        return True
    poly_area = poly.area
    if poly_area == 0:
        return True
    overrun = poly.difference(aoi_geom).area
    return overrun <= _AOI_COVER_REL_TOLERANCE * poly_area


def _generate_grid_over_aoi(
    aoi: ShapelyMultiPolygon,
    cell_size_m: float,
) -> list[ShapelyPolygon]:
    """Build a regular grid of square cells over the AOI bounding box,
    clip each cell to the AOI, and return the resulting polygons.

    Sizes are converted from meters using an equirectangular
    approximation:
      - 1° latitude  ≈ 111 320 m everywhere.
      - 1° longitude ≈ 111 320 · cos(centre latitude) m.

    Adequate for small project AOIs; a proper metric reprojection
    (pyproj) is required for global-scale precision.
    """
    minx, miny, maxx, maxy = aoi.bounds
    center_lat = (miny + maxy) / 2.0
    lat_step = cell_size_m / 111_320.0
    lon_step = cell_size_m / (
        111_320.0 * max(math.cos(math.radians(center_lat)), 0.01)
    )

    cells: list[ShapelyPolygon] = []
    # Safety cap for accidental large-AOI + small-cell combinations.
    cell_cap = 50_000

    y = miny
    while y < maxy:
        x = minx
        while x < maxx:
            cell = ShapelyPolygon(
                [
                    (x, y),
                    (x + lon_step, y),
                    (x + lon_step, y + lat_step),
                    (x, y + lat_step),
                    (x, y),
                ]
            )
            if cell.intersects(aoi):
                clipped = cell.intersection(aoi)
                if not clipped.is_empty and clipped.area > 0:
                    # `intersection` can return a Polygon, MultiPolygon,
                    # or GeometryCollection; retain polygon pieces only.
                    geoms = (
                        list(clipped.geoms)
                        if hasattr(clipped, "geoms")
                        else [clipped]
                    )
                    for piece in geoms:
                        if (
                            isinstance(piece, ShapelyPolygon)
                            and piece.area > 0
                        ):
                            cells.append(piece)
                            if len(cells) >= cell_cap:
                                return cells
            x += lon_step
        y += lat_step

    return cells


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TaskingTaskRepository:
    """Tasks, locks, changesets, and submit-flow operations.

    Methods assume the caller has already passed the workspace tenancy
    gate (`WorkspaceRepository.getById`); that check is performed at
    the route layer.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- common helpers ---------------------------------------------------

    async def _get_project(
        self, workspace_id: int, project_id: int
    ) -> TaskingProject:
        rs = await self.session.execute(
            select(TaskingProject).where(
                (TaskingProject.id == project_id)
                & (TaskingProject.workspace_id == workspace_id)
                & (TaskingProject.deleted_at.is_(None))
            )
        )
        project = rs.scalar_one_or_none()
        if project is None:
            raise NotFoundException(f"Project {project_id} not found")
        return project

    async def _get_task(
        self, project_id: int, task_number: int
    ) -> TaskingTask:
        rs = await self.session.execute(
            select(TaskingTask).where(
                (TaskingTask.project_id == project_id)
                & (TaskingTask.task_number == task_number)
            )
        )
        task = rs.scalar_one_or_none()
        if task is None:
            raise NotFoundException(
                f"Task {task_number} not found in project {project_id}"
            )
        return task

    async def _get_active_lock(
        self, task_id: int
    ) -> Optional[TaskingLock]:
        rs = await self.session.execute(
            select(TaskingLock).where(
                (TaskingLock.task_id == task_id)
                & (TaskingLock.released_at.is_(None))
            )
        )
        return rs.scalar_one_or_none()

    async def _get_active_lock_for_user_in_project(
        self, project_id: int, user_auth_uid: str
    ) -> Optional[TaskingLock]:
        rs = await self.session.execute(
            select(TaskingLock).where(
                (TaskingLock.project_id == project_id)
                & (TaskingLock.user_auth_uid == user_auth_uid)
                & (TaskingLock.released_at.is_(None))
            )
        )
        return rs.scalar_one_or_none()

    async def _project_role(
        self, project_id: int, user: UserInfo, workspace_id: int
    ) -> Optional[str]:
        """Effective project role: explicit row overrides workspace-level.

        Returns one of `lead`, `validator`, `contributor`, or `None`
        (outsider). Workspace LEAD beats an explicit non-lead row so
        a workspace lead is never accidentally demoted by a stale role
        assignment.
        """
        rs = await self.session.execute(
            text(
                "SELECT role FROM tasking_project_roles "
                "WHERE project_id = :pid AND user_auth_uid = :uid"
            ),
            {"pid": project_id, "uid": str(user.user_uuid)},
        )
        explicit = rs.scalar_one_or_none()

        # Workspace LEAD always wins.
        if user.isWorkspaceLead(workspace_id):
            return "lead"
        if explicit:
            return str(explicit)
        if user.isWorkspaceValidator(workspace_id):
            return "validator"
        if user.isWorkspaceContributor(workspace_id):
            return "contributor"
        return None

    async def _audit(
        self,
        *,
        event_type: str,
        project_id: int,
        task_id: Optional[int],
        actor_uuid: UUID,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO tasking_audit_events "
                "(event_type, project_id, task_id, actor_user_auth_uid, details) "
                "VALUES (:et, :pid, :tid, :uid, CAST(:dt AS jsonb))"
            ),
            {
                "et": event_type,
                "pid": project_id,
                "tid": task_id,
                "uid": str(actor_uuid),
                "dt": json.dumps(details or {}),
            },
        )

    async def _lookup_user_display(
        self, user_auth_uid: Optional[str]
    ) -> Optional[str]:
        if not user_auth_uid:
            return None
        rs = await self.session.execute(
            text(
                "SELECT display_name FROM users WHERE auth_uid = :uid"
            ),
            {"uid": user_auth_uid},
        )
        return rs.scalar_one_or_none()

    async def _to_task_response(self, task: TaskingTask) -> TaskResponse:
        geom_shape = to_shape(task.geometry) if task.geometry is not None else None
        if geom_shape is None or not isinstance(geom_shape, ShapelyPolygon):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Task geometry missing or non-Polygon",
            )

        lock_row = await self._get_active_lock(task.id)  # type: ignore[arg-type]
        lock_summary: Optional[TaskLockSummary] = None
        if lock_row is not None:
            display = await self._lookup_user_display(lock_row.user_auth_uid)
            lock_summary = TaskLockSummary(
                user_id=UUID(lock_row.user_auth_uid),
                user_name=display,
                locked_at=lock_row.locked_at,
                expires_at=lock_row.expires_at,
            )

        last_mapper: Optional[LastMapper] = None
        if task.last_mapper_id:
            display = await self._lookup_user_display(task.last_mapper_id)
            last_mapper = LastMapper(
                user_id=UUID(task.last_mapper_id), user_name=display
            )

        return TaskResponse(
            id=task.id,  # type: ignore[arg-type]
            task_number=task.task_number,
            status=task.status,
            geometry=_shapely_polygon_to_geojson(geom_shape),
            area_sqkm=float(task.area_sqkm),
            lock=lock_summary,
            last_mapper=last_mapper,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    # ---- grid generation -------------------------------------------------

    async def generate_grid(
        self,
        workspace_id: int,
        project_id: int,
        cell_size_m: int,
    ) -> TaskBoundariesFeatureCollection:
        """Preview-only grid generation: returns a FeatureCollection of
        clipped grid cells over the project AOI without persisting.

        Caller previews the returned shapes and then commits the same
        FeatureCollection via `POST /tasks/save` (with
        ``source: "grid"``). LEAD-only; project must be in `draft`
        with an AOI set.
        """
        project = await self._get_project(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tasks can only be generated while the project is in draft",
            )
        if project.aoi is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project AOI is required before generating a grid",
            )

        aoi_geom = to_shape(project.aoi)
        if isinstance(aoi_geom, ShapelyPolygon):
            aoi_geom = ShapelyMultiPolygon([aoi_geom])

        cells = _generate_grid_over_aoi(aoi_geom, float(cell_size_m))
        if not cells:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Grid produced no cells — AOI may be too small for "
                    "the chosen cellSizeMeters."
                ),
            )

        features = [
            TaskBoundaryFeature(
                type="Feature",
                geometry=_shapely_polygon_to_geojson(cell),
                properties={"cellIndex": idx},
            )
            for idx, cell in enumerate(cells)
        ]
        return TaskBoundariesFeatureCollection(
            type="FeatureCollection",
            features=features,
        )

    # ---- validate --------------------------------------------------------

    async def validate(
        self,
        workspace_id: int,
        project_id: int,
        fc: TaskBoundariesFeatureCollection,
    ) -> ValidatePreviewResponse:
        project = await self._get_project(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tasks can only be validated while the project is in draft",
            )
        if project.aoi is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project AOI is required before validating tasks",
            )

        aoi_geom = to_shape(project.aoi)
        if isinstance(aoi_geom, ShapelyPolygon):
            aoi_geom = ShapelyMultiPolygon([aoi_geom])

        warnings: list[ValidateWarning] = []
        for idx, feat in enumerate(fc.features):
            poly = _polygon_to_shapely(feat.geometry.model_dump())
            if not _aoi_covers(aoi_geom, poly):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Task feature {idx} is not fully inside the project AOI",
                )
            area_km2 = _polygon_area_km2(poly)
            if area_km2 > _GRID_MAX_KM2:
                warnings.append(
                    ValidateWarning(
                        task_index=idx,
                        issue="polygon_exceeds_grid_size",
                        area_sqkm=round(area_km2, 4),
                    )
                )

        return ValidatePreviewResponse(
            valid=True,
            warnings=warnings,
            source="import",
            feature_collection=fc,
        )

    # ---- save ------------------------------------------------------------

    async def save(
        self,
        workspace_id: int,
        project_id: int,
        current_user: UserInfo,
        body: SaveTasksRequest,
        idempotency_key: Optional[str],
    ) -> tuple[SaveTasksResponse, bool]:
        """Bulk-insert tasks. Returns (response, replayed)."""
        project = await self._get_project(workspace_id, project_id)
        if project.status != ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tasks can only be saved while the project is in draft",
            )
        if project.aoi is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Project AOI is required before saving tasks",
            )

        body_bytes = json.dumps(
            body.model_dump(mode="json"), sort_keys=True
        ).encode()
        body_hash = hashlib.sha256(body_bytes).hexdigest()

        # Idempotent replay path.
        if idempotency_key:
            rs = await self.session.execute(
                text(
                    "SELECT body_hash, response_json "
                    "FROM tasking_task_save_idempotency "
                    "WHERE project_id = :pid AND key = :k"
                ),
                {"pid": project.id, "k": idempotency_key},
            )
            row = rs.first()
            if row is not None:
                if row.body_hash != body_hash:
                    raise AlreadyExistsException(
                        "Idempotency key reused with a different request"
                    )
                stored = row.response_json
                if isinstance(stored, str):
                    stored = json.loads(stored)
                # Stored payload was serialised with `replayed=False`
                # at first-write time; set it to True on replay so the
                # caller can distinguish a replay from a fresh save.
                stored["replayed"] = True
                return SaveTasksResponse(**stored), True

        # Refuse if tasks already exist (re-upload AOI to wipe).
        existing = await self.session.execute(
            text(
                "SELECT 1 FROM tasking_tasks WHERE project_id = :pid LIMIT 1"
            ),
            {"pid": project.id},
        )
        if existing.scalar() is not None:
            raise AlreadyExistsException("Tasks already saved")

        # Re-validate every feature against AOI (atomic guarantee).
        aoi_geom = to_shape(project.aoi)
        if isinstance(aoi_geom, ShapelyPolygon):
            aoi_geom = ShapelyMultiPolygon([aoi_geom])

        created: list[TaskingTask] = []
        for idx, feat in enumerate(body.feature_collection.features):
            poly = _polygon_to_shapely(feat.geometry.model_dump())
            if not _aoi_covers(aoi_geom, poly):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Task feature {idx} is not fully inside the project AOI",
                )
            task = TaskingTask(
                project_id=project.id,  # type: ignore[arg-type]
                task_number=idx + 1,
                area_sqkm=round(_polygon_area_km2(poly), 4),
                status=TaskStatus.TO_MAP,
                geometry=from_shape(poly, srid=4326),
            )
            self.session.add(task)
            created.append(task)

        await self.session.flush()  # populate ids

        # Set boundary type + bump project updated_at.
        await self.session.execute(
            update(TaskingProject)
            .where(TaskingProject.id == project.id)
            .values(
                task_boundary_type=body.source,
                updated_at=datetime.now(),
            )
        )

        # Audit one row per task.
        for t in created:
            await self._audit(
                event_type="task_created",
                project_id=project.id,  # type: ignore[arg-type]
                task_id=t.id,
                actor_uuid=current_user.user_uuid,
                details={"taskNumber": t.task_number},
            )

        task_responses = [await self._to_task_response(t) for t in created]
        response = SaveTasksResponse(
            project_id=project.id,  # type: ignore[arg-type]
            task_boundary_type=body.source,
            task_count=len(created),
            tasks=task_responses,
            idempotency_key=idempotency_key,
            replayed=False,
        )

        # Persist idempotency record (committed in the same txn).
        if idempotency_key:
            payload = response.model_dump(mode="json")
            await self.session.execute(
                text(
                    "INSERT INTO tasking_task_save_idempotency "
                    "(project_id, key, body_hash, response_json) "
                    "VALUES (:pid, :k, :bh, CAST(:rj AS jsonb))"
                ),
                {
                    "pid": project.id,
                    "k": idempotency_key,
                    "bh": body_hash,
                    "rj": json.dumps(payload),
                },
            )

        await self.session.commit()
        return response, False

    # ---- list / get ------------------------------------------------------

    async def list_tasks(
        self,
        workspace_id: int,
        project_id: int,
        *,
        status_filter: Optional[TaskStatus] = None,
        locked_by_user_id: Optional[UUID] = None,
        last_mapper_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 200,
    ) -> TaskListResponse:
        await self._get_project(workspace_id, project_id)

        where = TaskingTask.project_id == project_id
        if status_filter is not None:
            where = where & (TaskingTask.status == status_filter)
        if last_mapper_id is not None:
            where = where & (TaskingTask.last_mapper_id == str(last_mapper_id))

        total_q = await self.session.execute(
            select(func.count()).select_from(TaskingTask).where(where)
        )
        total = int(total_q.scalar() or 0)

        page = max(page, 1)
        page_size = max(min(page_size, 1000), 1)
        offset = (page - 1) * page_size

        rows = await self.session.execute(
            select(TaskingTask)
            .where(where)
            .order_by(TaskingTask.task_number.asc())
            .limit(page_size)
            .offset(offset)
        )
        tasks = list(rows.scalars().all())

        responses: list[TaskResponse] = []
        for t in tasks:
            tr = await self._to_task_response(t)
            if locked_by_user_id is not None and (
                tr.lock is None or tr.lock.user_id != locked_by_user_id
            ):
                continue
            responses.append(tr)

        return TaskListResponse(
            tasks=responses,
            pagination=Pagination(page=page, page_size=page_size, total=total),
        )

    async def get_task(
        self, workspace_id: int, project_id: int, task_number: int
    ) -> TaskResponse:
        await self._get_project(workspace_id, project_id)
        task = await self._get_task(project_id, task_number)
        return await self._to_task_response(task)

    # ---- lock / unlock / extend / reset ----------------------------------

    async def lock_task(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        current_user: UserInfo,
    ) -> TaskResponse:
        project = await self._get_project(workspace_id, project_id)
        if project.status != ProjectStatus.OPEN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project is not open",
            )

        task = await self._get_task(project_id, task_number)

        # Eligibility table.
        role = await self._project_role(
            project_id, current_user, workspace_id
        )
        if role is None:
            raise ForbiddenException("User has no access to this project")

        if task.status in (TaskStatus.TO_MAP, TaskStatus.TO_REMAP):
            if role not in ("contributor", "validator", "lead"):
                raise ForbiddenException(
                    "Role does not permit locking this task for mapping"
                )
        elif task.status == TaskStatus.TO_REVIEW:
            if role not in ("validator", "lead"):
                raise ForbiddenException(
                    "Role does not permit locking this task for validation"
                )
            if (
                task.last_mapper_id
                and task.last_mapper_id == str(current_user.user_uuid)
            ):
                raise ForbiddenException(
                    "Cannot validate a task you last mapped"
                )
        else:
            raise ForbiddenException("Task is in a terminal state")

        # One active lock per task.
        if await self._get_active_lock(task.id) is not None:  # type: ignore[arg-type]
            raise AlreadyExistsException("Task is already locked")

        # One active lock per (project, user).
        other = await self._get_active_lock_for_user_in_project(
            project_id, str(current_user.user_uuid)
        )
        if other is not None:
            other_task_rs = await self.session.execute(
                select(TaskingTask).where(TaskingTask.id == other.task_id)
            )
            other_task = other_task_rs.scalar_one()
            summary = ExistingLockSummary(
                task_number=other_task.task_number,
                task_status=other_task.status,
                locked_at=other.locked_at,
                expires_at=other.expires_at,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "User already holds a lock in this project",
                    "existing_lock": summary.model_dump(mode="json"),
                },
            )

        now = datetime.now()
        expires_at = now + timedelta(hours=project.lock_timeout_hours)
        lock = TaskingLock(
            task_id=task.id,  # type: ignore[arg-type]
            project_id=project_id,
            user_auth_uid=str(current_user.user_uuid),
            task_status_at_lock=task.status,
            locked_at=now,
            expires_at=expires_at,
        )

        try:
            self.session.add(lock)
            await self.session.flush()
            await self._audit(
                event_type="task_locked",
                project_id=project_id,
                task_id=task.id,
                actor_uuid=current_user.user_uuid,
                details={"taskNumber": task.task_number},
            )
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise AlreadyExistsException(
                "Task is already locked or user already holds a lock in this project"
            )

        return await self._to_task_response(task)

    async def unlock_task(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        current_user: UserInfo,
        force: bool = False,
    ) -> None:
        await self._get_project(workspace_id, project_id)
        task = await self._get_task(project_id, task_number)
        lock = await self._get_active_lock(task.id)  # type: ignore[arg-type]
        if lock is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task has no active lock",
            )

        if force:
            if not current_user.isWorkspaceLead(workspace_id):
                raise ForbiddenException("Only LEAD may force-release a lock")
            release_reason = LockReleaseReason.LEAD_RELEASE
        else:
            if lock.user_auth_uid != str(current_user.user_uuid):
                raise ForbiddenException("Only the lock holder may release it")
            release_reason = LockReleaseReason.MANUAL

        now = datetime.now()
        await self.session.execute(
            update(TaskingLock)
            .where(TaskingLock.id == lock.id)
            .values(released_at=now, release_reason=release_reason)
        )
        await self._audit(
            event_type="task_unlocked",
            project_id=project_id,
            task_id=task.id,
            actor_uuid=current_user.user_uuid,
            details={
                "taskNumber": task.task_number,
                "releaseReason": release_reason.value,
            },
        )
        await self.session.commit()

    async def extend_lock(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        current_user: UserInfo,
    ) -> TaskResponse:
        project = await self._get_project(workspace_id, project_id)
        task = await self._get_task(project_id, task_number)
        lock = await self._get_active_lock(task.id)  # type: ignore[arg-type]
        if lock is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task has no active lock to extend",
            )
        if lock.user_auth_uid != str(current_user.user_uuid):
            raise ForbiddenException("Only the lock holder may extend the lock")

        # `expires_at` comes back tz-aware from Postgres (TIMESTAMPTZ);
        # the SQLModel column is typed as naive `datetime`. Strip tzinfo
        # before re-binding to avoid asyncpg's offset-aware/naive
        # mismatch error.
        prev = lock.expires_at
        if prev.tzinfo is not None:
            prev = prev.replace(tzinfo=None)
        new_expiry = prev + timedelta(hours=project.lock_timeout_hours)
        await self.session.execute(
            update(TaskingLock)
            .where(TaskingLock.id == lock.id)
            .values(expires_at=new_expiry)
        )
        await self._audit(
            event_type="task_lock_extended",
            project_id=project_id,
            task_id=task.id,
            actor_uuid=current_user.user_uuid,
            details={
                "taskNumber": task.task_number,
                "expiresAt": new_expiry.isoformat(),
            },
        )
        await self.session.commit()
        return await self._to_task_response(task)

    async def reset_task(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        current_user: UserInfo,
    ) -> TaskResponse:
        project = await self._get_project(workspace_id, project_id)
        if project.status == ProjectStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot reset a task while the project is in draft",
            )

        task = await self._get_task(project_id, task_number)

        now = datetime.now()
        # Release any active lock.
        lock = await self._get_active_lock(task.id)  # type: ignore[arg-type]
        if lock is not None:
            await self.session.execute(
                update(TaskingLock)
                .where(TaskingLock.id == lock.id)
                .values(
                    released_at=now,
                    release_reason=LockReleaseReason.RESET,
                )
            )
            await self._audit(
                event_type="task_unlocked",
                project_id=project_id,
                task_id=task.id,
                actor_uuid=current_user.user_uuid,
                details={
                    "taskNumber": task.task_number,
                    "releaseReason": LockReleaseReason.RESET.value,
                },
            )

        previous_status = task.status
        if previous_status != TaskStatus.TO_MAP:
            await self.session.execute(
                update(TaskingTask)
                .where(TaskingTask.id == task.id)
                .values(
                    status=TaskStatus.TO_MAP,
                    last_mapper_id=None,
                    updated_at=now,
                )
            )
            await self._audit(
                event_type="task_state_changed",
                project_id=project_id,
                task_id=task.id,
                actor_uuid=current_user.user_uuid,
                details={
                    "taskNumber": task.task_number,
                    "from": previous_status.value,
                    "to": TaskStatus.TO_MAP.value,
                },
            )
        else:
            # Clear last_mapper_id even if state was already to_map.
            await self.session.execute(
                update(TaskingTask)
                .where(TaskingTask.id == task.id)
                .values(last_mapper_id=None, updated_at=now)
            )

        await self._audit(
            event_type="task_reset",
            project_id=project_id,
            task_id=task.id,
            actor_uuid=current_user.user_uuid,
            details={"taskNumber": task.task_number},
        )

        await self.session.commit()
        refreshed = await self._get_task(project_id, task_number)
        return await self._to_task_response(refreshed)

    # ---- submit ----------------------------------------------------------

    async def submit(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        current_user: UserInfo,
        body: SubmitRequest,
    ) -> TaskResponse:
        project = await self._get_project(workspace_id, project_id)
        task = await self._get_task(project_id, task_number)
        lock = await self._get_active_lock(task.id)  # type: ignore[arg-type]
        if lock is None or lock.user_auth_uid != str(current_user.user_uuid):
            raise ForbiddenException("Caller does not hold the active lock")

        # Effective role for the *current* task status — drives the
        # state transition table.
        if task.status in (TaskStatus.TO_MAP, TaskStatus.TO_REMAP):
            actor_role = "mapper"
        elif task.status == TaskStatus.TO_REVIEW:
            actor_role = "validator"
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Task is in a terminal state",
            )

        # Feedback is only meaningful in validator context.
        # if body.feedback is not None and actor_role != "validator":
        #     raise HTTPException(
        #         status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        #         detail="feedback is only accepted in validator context",
        #     )

        now = datetime.now()

        # Record the changeset row.
        cs = TaskingChangeset(
            task_id=task.id,  # type: ignore[arg-type]
            project_id=project_id,
            lock_id=lock.id,  # type: ignore[arg-type]
            user_auth_uid=str(current_user.user_uuid),
            osm_changeset_id=body.osm_changeset_id,
            submitted_at=now,
        )
        self.session.add(cs)
        await self.session.flush()

        await self._audit(
            event_type="changeset_submitted",
            project_id=project_id,
            task_id=task.id,
            actor_uuid=current_user.user_uuid,
            details={
                "taskNumber": task.task_number,
                "osmChangesetId": body.osm_changeset_id,
                "done": body.done,
            },
        )

        if not body.done:
            # Slide lock expiry from submitted_at + lock_timeout_hours.
            new_expiry = now + timedelta(hours=project.lock_timeout_hours)
            await self.session.execute(
                update(TaskingLock)
                .where(TaskingLock.id == lock.id)
                .values(expires_at=new_expiry)
            )
            await self._audit(
                event_type="task_lock_renewed",
                project_id=project_id,
                task_id=task.id,
                actor_uuid=current_user.user_uuid,
                details={
                    "taskNumber": task.task_number,
                    "expiresAt": new_expiry.isoformat(),
                },
            )
            await self.session.commit()
            refreshed = await self._get_task(project_id, task_number)
            return await self._to_task_response(refreshed)

        # done = True → resolve next status per transition table.
        previous_status = task.status
        new_last_mapper = task.last_mapper_id

        if actor_role == "mapper":
            new_last_mapper = str(current_user.user_uuid)
            if previous_status == TaskStatus.TO_MAP:
                new_status = (
                    TaskStatus.TO_REVIEW
                    if project.review_required
                    else TaskStatus.COMPLETED
                )
            else:  # to_remap
                new_status = TaskStatus.TO_REVIEW
        else:  # validator
            if body.feedback is not None:
                if body.feedback.reason_category is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="feedback.reasonCategory is required when sending feedback",
                    )
                # Insert the remap feedback row.
                self.session.add(
                    TaskingFeedback(
                        task_id=task.id,  # type: ignore[arg-type]
                        project_id=project_id,
                        author_user_auth_uid=str(current_user.user_uuid),
                        reason_category=body.feedback.reason_category,
                        notes=body.feedback.notes,
                        created_at=now,
                    )
                )
                await self._audit(
                    event_type="feedback_submitted",
                    project_id=project_id,
                    task_id=task.id,
                    actor_uuid=current_user.user_uuid,
                    details={
                        "taskNumber": task.task_number,
                        "reasonCategory": body.feedback.reason_category.value,
                    },
                )
                new_status = TaskStatus.TO_REMAP
            else:
                new_status = TaskStatus.COMPLETED

        # Release the lock (auto_unlock).
        await self.session.execute(
            update(TaskingLock)
            .where(TaskingLock.id == lock.id)
            .values(
                released_at=now,
                release_reason=LockReleaseReason.AUTO_UNLOCK,
            )
        )
        await self._audit(
            event_type="task_unlocked",
            project_id=project_id,
            task_id=task.id,
            actor_uuid=current_user.user_uuid,
            details={
                "taskNumber": task.task_number,
                "releaseReason": LockReleaseReason.AUTO_UNLOCK.value,
            },
        )

        # Apply state transition.
        await self.session.execute(
            update(TaskingTask)
            .where(TaskingTask.id == task.id)
            .values(
                status=new_status,
                last_mapper_id=new_last_mapper,
                updated_at=now,
            )
        )
        if new_status != previous_status:
            await self._audit(
                event_type="task_state_changed",
                project_id=project_id,
                task_id=task.id,
                actor_uuid=current_user.user_uuid,
                details={
                    "taskNumber": task.task_number,
                    "from": previous_status.value,
                    "to": new_status.value,
                },
            )

        await self.session.commit()
        refreshed = await self._get_task(project_id, task_number)
        return await self._to_task_response(refreshed)


__all__ = ["TaskingTaskRepository"]
