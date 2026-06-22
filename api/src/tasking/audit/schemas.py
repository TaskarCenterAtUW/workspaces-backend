from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AuditEventType(StrEnum):
    """Closed set of event types written to `tasking_audit_events`.

    Mirrors the spec's `AuditEventType` enum; values match the literal
    strings persisted in the `event_type` column.
    """

    PROJECT_CREATED = "project_created"
    PROJECT_ACTIVATED = "project_activated"
    PROJECT_CLOSED = "project_closed"
    PROJECT_EDITED = "project_edited"
    PROJECT_DELETED = "project_deleted"
    PROJECT_RESET = "project_reset"
    AOI_UPLOADED = "aoi_uploaded"
    AOI_DELETED = "aoi_deleted"
    TASKS_CREATED = "tasks_created"
    TASK_STATE_CHANGED = "task_state_changed"
    TASK_LOCKED = "task_locked"
    TASK_LOCK_EXTENDED = "task_lock_extended"
    TASK_LOCK_RENEWED = "task_lock_renewed"
    TASK_UNLOCKED = "task_unlocked"
    TASK_RESET = "task_reset"
    CHANGESET_SUBMITTED = "changeset_submitted"
    FEEDBACK_SUBMITTED = "feedback_submitted"


class TaskingAuditEvent(SQLModel, table=True):
    """Read-only mirror of `tasking_audit_events`.

    Writes happen via raw SQL in the task/project repositories so the
    insert path stays decoupled from this module. The table is FK-free
    by design (it must survive a project soft-delete + child hard-delete).
    """

    __tablename__ = "tasking_audit_events"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(max_length=64, nullable=False)
    project_id: int = Field(nullable=False, index=True)
    task_id: Optional[int] = Field(default=None, nullable=True)
    actor_user_auth_uid: UUID = Field(nullable=False)
    occurred_at: datetime = Field(nullable=False)
    details: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    project_deleted: bool = Field(default=False, nullable=False)


__all__ = ["AuditEventType", "TaskingAuditEvent"]
