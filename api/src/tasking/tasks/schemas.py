from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Optional

from geoalchemy2 import Geometry
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Enums (mirrors of postgres enums in the migration)
# ---------------------------------------------------------------------------


class TaskStatus(StrEnum):
    TO_MAP = "to_map"
    TO_REVIEW = "to_review"
    TO_REMAP = "to_remap"
    COMPLETED = "completed"


class LockReleaseReason(StrEnum):
    AUTO_UNLOCK = "auto_unlock"
    MANUAL = "manual"
    LEAD_RELEASE = "lead_release"
    STALE_TIMEOUT = "stale_timeout"
    RESET = "reset"


class FeedbackReason(StrEnum):
    INCOMPLETE_MAPPING = "incomplete_mapping"
    DATA_QUALITY_ISSUE = "data_quality_issue"
    WRONG_AREA = "wrong_area"
    OTHER = "other"


def _task_status_column(*, nullable: bool = False) -> Column:
    return Column(
        SAEnum(
            TaskStatus,
            name="tasking_task_status",
            create_type=False,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        nullable=nullable,
    )


def _release_reason_column() -> Column:
    return Column(
        SAEnum(
            LockReleaseReason,
            name="tasking_lock_release_reason",
            create_type=False,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        nullable=True,
    )


def _feedback_reason_column() -> Column:
    return Column(
        SAEnum(
            FeedbackReason,
            name="tasking_feedback_reason",
            create_type=False,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        nullable=True,
    )


# ---------------------------------------------------------------------------
# Table models
# ---------------------------------------------------------------------------


class TaskingTask(SQLModel, table=True):
    """Per-project task polygon — saved as part of a bulk batch."""

    __tablename__ = "tasking_tasks"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(nullable=False, index=True)
    task_number: int = Field(nullable=False)
    area_sqkm: Decimal = Field(nullable=False)

    status: TaskStatus = Field(
        default=TaskStatus.TO_MAP,
        sa_column=_task_status_column(),
    )

    last_mapper_id: Optional[str] = Field(default=None, nullable=True)

    geometry: Optional[Any] = Field(
        default=None,
        sa_column=Column(
            Geometry(geometry_type="POLYGON", srid=4326),
            nullable=False,
        ),
    )

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False},
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False, "onupdate": datetime.now},
    )


class TaskingLock(SQLModel, table=True):
    """Active / historical lock on a task.

    Active rows have `released_at IS NULL`. Two partial unique indexes
    enforce: at most one active lock per task, and at most one active
    lock per (project, user).
    """

    __tablename__ = "tasking_locks"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(nullable=False)
    project_id: int = Field(nullable=False)
    user_auth_uid: str = Field(nullable=False)

    task_status_at_lock: TaskStatus = Field(
        sa_column=_task_status_column(),
    )

    locked_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False},
    )
    expires_at: datetime = Field(nullable=False)
    released_at: Optional[datetime] = None

    release_reason: Optional[LockReleaseReason] = Field(
        default=None,
        sa_column=_release_reason_column(),
    )


class TaskingChangeset(SQLModel, table=True):
    """One row per `/submit` call — links a lock session to an OSM changeset."""

    __tablename__ = "tasking_changesets"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(nullable=False)
    project_id: int = Field(nullable=False)
    lock_id: int = Field(nullable=False)
    user_auth_uid: str = Field(nullable=False)
    osm_changeset_id: int = Field(nullable=False)

    submitted_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False},
    )


class TaskingFeedback(SQLModel, table=True):
    """Per-task feedback row (remap rejections + free-form notes)."""

    __tablename__ = "tasking_feedback"  # type: ignore[assignment]

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: int = Field(nullable=False)
    project_id: int = Field(nullable=False)
    author_user_auth_uid: str = Field(nullable=False)

    reason_category: Optional[FeedbackReason] = Field(
        default=None,
        sa_column=_feedback_reason_column(),
    )
    notes: str = Field(nullable=False)

    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column_kwargs={"nullable": False},
    )


__all__ = [
    "FeedbackReason",
    "LockReleaseReason",
    "TaskStatus",
    "TaskingChangeset",
    "TaskingFeedback",
    "TaskingLock",
    "TaskingTask",
]
