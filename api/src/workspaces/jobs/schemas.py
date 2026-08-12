from datetime import datetime
from typing import Any

from sqlalchemy import JSON as SAJson
from sqlalchemy import Column
from sqlmodel import Field, Relationship, SQLModel


class Job(SQLModel, table=True):
    __tablename__ = "jobs"

    id: int = Field(default=None, primary_key=True)
    job_type: str = Field(default=None, nullable=False)
    status: str = Field(default=None, nullable=False)
    request: dict = Field(default=None, sa_column=Column(SAJson, nullable=False))
    created_at: datetime = Field(sa_column=Column(nullable=False, default=datetime.now))
    updated_at: datetime = Field(sa_column=Column(nullable=False, default=datetime.now))
    current_task: str = Field(default=None, nullable=True)
    current_task_status: str = Field(default=None, nullable=True)
    response: dict = Field(default=None, sa_column=Column(SAJson, nullable=True))
    workspace_id: int = Field(default=None)  # Not sure if we have to add index here.

    # workspace_id: int = Field(foreign_key="workspaces.id")
    # workspace: "Workspace" = Relationship(back_populates="jobs")


class JobCreate(SQLModel):
    """Fields the client may supply when creating a job."""

    job_type: str
    status: str
    request: dict[str, Any]
    workspace_id: int
    current_task: str | None = None
    current_task_status: str | None = None
    response: dict[str, Any] | None = None


class JobPatch(SQLModel):
    """Fields the client may supply when updating a job."""

    job_type: str | None = None
    status: str | None = None
    request: dict[str, Any] | None = None
    current_task: str | None = None
    current_task_status: str | None = None
    response: dict[str, Any] | None = None
