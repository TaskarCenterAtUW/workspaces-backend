from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import NotFoundException
from api.src.tasking.audit.dtos import (
    ActorRef,
    AuditEvent,
    AuditEventListResponse,
)
from api.src.tasking.audit.schemas import AuditEventType
from api.src.tasking.projects.dtos import Pagination


_ALLOWED_ORDER_DIR = {"ASC", "DESC"}


def _normalise_dir(order_dir: str) -> str:
    return order_dir.upper() if order_dir.upper() in _ALLOWED_ORDER_DIR else "DESC"


def _clamp_page(page: int, page_size: int, max_page_size: int) -> tuple[int, int]:
    return max(page, 1), max(min(page_size, max_page_size), 1)


class TaskingAuditRepository:
    """Read-side queries for `tasking_audit_events`.

    Writes are owned by the task and project repositories — see
    `_audit()` in `api/src/tasking/tasks/repository.py`. This module
    only reads.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- internal helpers -------------------------------------------------

    async def _assert_project_visible(
        self, workspace_id: int, project_id: int, *, include_deleted: bool
    ) -> None:
        """Confirm the project lives in the workspace; honour soft-delete
        unless the caller explicitly opted into deleted projects.
        """
        clause = (
            "SELECT 1 FROM tasking_projects "
            "WHERE id = :pid AND workspace_id = :wid"
        )
        if not include_deleted:
            clause += " AND deleted_at IS NULL"

        result = await self.session.execute(
            text(clause), {"pid": project_id, "wid": workspace_id}
        )
        if result.scalar() is None:
            raise NotFoundException(f"Project {project_id} not found")

    async def _task_id_from_number(
        self, project_id: int, task_number: int
    ) -> int:
        result = await self.session.execute(
            text(
                "SELECT id FROM tasking_tasks "
                "WHERE project_id = :pid AND task_number = :tn"
            ),
            {"pid": project_id, "tn": task_number},
        )
        task_id = result.scalar()
        if task_id is None:
            raise NotFoundException(
                f"Task {task_number} not found on project {project_id}"
            )
        return int(task_id)

    async def _resolve_actor_names(
        self, auth_uids: set[str]
    ) -> dict[str, Optional[str]]:
        """Batch-load `users.display_name` for every distinct actor.

        Avoids the N+1 fetch when rendering long event lists. UIDs with
        no `users` row map to None (the field is optional).
        """
        if not auth_uids:
            return {}
        rows = await self.session.execute(
            text(
                "SELECT auth_uid, display_name FROM users "
                "WHERE auth_uid = ANY(:uids)"
            ),
            {"uids": list(auth_uids)},
        )
        mapping = {row[0]: row[1] for row in rows.all()}
        # Unknown actors still need a None entry so callers can `.get(uid)`.
        for uid in auth_uids:
            mapping.setdefault(uid, None)
        return mapping

    @staticmethod
    def _row_to_event(
        row,
        names: dict[str, Optional[str]],
    ) -> AuditEvent:
        (
            event_id,
            event_type,
            project_id,
            task_id,
            actor_uid,
            occurred_at,
            details,
            project_deleted,
        ) = row
        details = details or {}
        task_number = details.get("task_number") if isinstance(details, dict) else None
        return AuditEvent(
            id=event_id,
            event_type=AuditEventType(event_type),
            project_id=project_id,
            task_id=task_id,
            task_number=task_number,
            actor=ActorRef(
                user_id=UUID(str(actor_uid)),
                display_name=names.get(str(actor_uid)),
            ),
            occurred_at=occurred_at,
            details=details,
            project_deleted=bool(project_deleted),
        )

    # ---- listing queries --------------------------------------------------

    async def list_project_events(
        self,
        workspace_id: int,
        project_id: int,
        *,
        event_type: Optional[AuditEventType] = None,
        task_number: Optional[int] = None,
        actor_user_id: Optional[UUID] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 50,
        order_dir: str = "DESC",
    ) -> AuditEventListResponse:
        await self._assert_project_visible(
            workspace_id, project_id, include_deleted=include_deleted
        )

        page, page_size = _clamp_page(page, page_size, max_page_size=200)
        order_dir = _normalise_dir(order_dir)

        # `task_number` is stored inside `details` JSONB rather than as a
        # column, so filter via the typed accessor.
        where = ["project_id = :pid"]
        params: dict = {"pid": project_id}
        if event_type is not None:
            where.append("event_type = :et")
            params["et"] = event_type.value
        if task_number is not None:
            where.append("(details->>'task_number')::int = :tn")
            params["tn"] = task_number
        if actor_user_id is not None:
            where.append("actor_user_auth_uid = :au")
            params["au"] = str(actor_user_id)
        if occurred_from is not None:
            where.append("occurred_at >= :from_ts")
            params["from_ts"] = occurred_from
        if occurred_to is not None:
            where.append("occurred_at <= :to_ts")
            params["to_ts"] = occurred_to
        where_sql = " AND ".join(where)

        total_q = await self.session.execute(
            text(f"SELECT COUNT(*) FROM tasking_audit_events WHERE {where_sql}"),
            params,
        )
        total = int(total_q.scalar() or 0)

        rows = await self.session.execute(
            text(
                "SELECT id, event_type, project_id, task_id, "
                "       actor_user_auth_uid, occurred_at, details, project_deleted "
                "FROM tasking_audit_events "
                f"WHERE {where_sql} "
                f"ORDER BY occurred_at {order_dir}, id {order_dir} "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        raw_rows = list(rows.all())
        actor_uids = {str(r[4]) for r in raw_rows}
        names = await self._resolve_actor_names(actor_uids)

        return AuditEventListResponse(
            results=[self._row_to_event(r, names) for r in raw_rows],
            pagination=Pagination(page=page, page_size=page_size, total=total),
        )

    async def list_task_events(
        self,
        workspace_id: int,
        project_id: int,
        task_number: int,
        *,
        event_type: Optional[AuditEventType] = None,
        actor_user_id: Optional[UUID] = None,
        occurred_from: Optional[datetime] = None,
        occurred_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 25,
        order_dir: str = "DESC",
    ) -> AuditEventListResponse:
        # Task-level listings only ever surface live projects.
        await self._assert_project_visible(
            workspace_id, project_id, include_deleted=False
        )
        task_id = await self._task_id_from_number(project_id, task_number)

        page, page_size = _clamp_page(page, page_size, max_page_size=200)
        order_dir = _normalise_dir(order_dir)

        # Match either `task_id = :tid` or the `task_number` in `details`
        # — early audit rows for task creation set task_id but later
        # rows that reference a task (e.g. project-level lock-extensions)
        # may only persist the task_number. Both forms point at the
        # same task, so OR them.
        where = [
            "project_id = :pid",
            "(task_id = :tid OR (details->>'task_number')::int = :tn)",
        ]
        params: dict = {"pid": project_id, "tid": task_id, "tn": task_number}
        if event_type is not None:
            where.append("event_type = :et")
            params["et"] = event_type.value
        if actor_user_id is not None:
            where.append("actor_user_auth_uid = :au")
            params["au"] = str(actor_user_id)
        if occurred_from is not None:
            where.append("occurred_at >= :from_ts")
            params["from_ts"] = occurred_from
        if occurred_to is not None:
            where.append("occurred_at <= :to_ts")
            params["to_ts"] = occurred_to
        where_sql = " AND ".join(where)

        total_q = await self.session.execute(
            text(f"SELECT COUNT(*) FROM tasking_audit_events WHERE {where_sql}"),
            params,
        )
        total = int(total_q.scalar() or 0)

        rows = await self.session.execute(
            text(
                "SELECT id, event_type, project_id, task_id, "
                "       actor_user_auth_uid, occurred_at, details, project_deleted "
                "FROM tasking_audit_events "
                f"WHERE {where_sql} "
                f"ORDER BY occurred_at {order_dir}, id {order_dir} "
                "LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )
        raw_rows = list(rows.all())
        actor_uids = {str(r[4]) for r in raw_rows}
        names = await self._resolve_actor_names(actor_uids)

        return AuditEventListResponse(
            results=[self._row_to_event(r, names) for r in raw_rows],
            pagination=Pagination(page=page, page_size=page_size, total=total),
        )


__all__ = ["TaskingAuditRepository"]
