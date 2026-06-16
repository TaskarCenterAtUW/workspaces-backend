from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from api.src.tasking.audit.schemas import AuditEventType
from api.src.tasking.projects.dtos import Pagination, WireModel


class ActorRef(WireModel):
    """Resolved actor for an audit event."""

    user_id: UUID
    display_name: Optional[str] = None


class AuditEvent(WireModel):
    """One row in `tasking_audit_events`, with `actor` joined for display."""

    id: int
    event_type: AuditEventType
    project_id: int
    task_id: Optional[int] = None
    task_number: Optional[int] = None
    actor: ActorRef
    occurred_at: datetime
    details: dict[str, Any]
    project_deleted: bool = False


class AuditEventListResponse(WireModel):
    results: list[AuditEvent]
    pagination: Pagination


__all__ = [
    "ActorRef",
    "AuditEvent",
    "AuditEventListResponse",
]
