from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Fake workspace repository — tenancy gate without a DB.
# ---------------------------------------------------------------------------


class FakeWorkspaceRepo:
    """Mirrors ``WorkspaceRepository.getById`` semantics.

    Returns a stub workspace if the caller's accessibleWorkspaceIds
    contain the requested id; raises ``NotFoundException`` otherwise.
    """

    async def getById(self, current_user, workspace_id: int):
        from api.core.exceptions import NotFoundException

        all_ids = {
            wid for ids in current_user.accessibleWorkspaceIds.values() for wid in ids
        }
        if workspace_id not in all_ids:
            raise NotFoundException(f"Workspace {workspace_id} not found")
        # Echo the project-group id the user is a member of (any one
        # works for unit tests — only the project-create route reads it).
        pg_id = (
            next(iter(current_user.accessibleWorkspaceIds.keys()))
            if current_user.accessibleWorkspaceIds
            else "00000000-0000-0000-0000-000000000000"
        )
        return SimpleNamespace(id=workspace_id, tdeiProjectGroupId=pg_id)


# ---------------------------------------------------------------------------
# Fake project repository — in-memory storage of project rows.
# ---------------------------------------------------------------------------


class FakeProjectRepo:
    """In-memory stand-in for ``TaskingProjectRepository``.

    Stores projects in a dict keyed by id; returns ``ProjectResponse`` /
    ``ProjectListResponse`` exactly as the real repo would. Does NOT
    exercise PostGIS, ``ON CONFLICT``, soft-delete predicates, or
    cross-table joins — those are integration-suite territory.
    """

    def __init__(self) -> None:
        self._projects: dict[int, dict[str, Any]] = {}
        self._aois: dict[int, dict[str, Any]] = {}
        self._next_id = 1

    # ---- helpers --------------------------------------------------------

    def _response(self, p: dict[str, Any]):
        from api.src.tasking.projects.dtos import ProjectResponse

        return ProjectResponse(
            id=p["id"],
            workspace_id=p["workspace_id"],
            name=p["name"],
            instructions=p.get("instructions"),
            status=p["status"],
            review_required=p["review_required"],
            lock_timeout_hours=p["lock_timeout_hours"],
            task_boundary_type=p.get("task_boundary_type"),
            has_aoi=p["id"] in self._aois,
            task_count=p.get("task_count", 0),
            created_by=p["created_by"],
            created_by_name=p.get("created_by_name"),
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )

    # ---- create / list / get / patch / delete --------------------------

    async def create(
        self,
        workspace_id: int,
        current_user,
        body,
        tdei_project_group_id: str | None = None,
    ):
        from api.core.exceptions import AlreadyExistsException

        if any(
            p["workspace_id"] == workspace_id and p["name"] == body.name
            for p in self._projects.values()
        ):
            raise AlreadyExistsException(
                "A project with this name already exists in the workspace"
            )
        pid = self._next_id
        self._next_id += 1
        now = datetime.now()
        self._projects[pid] = {
            "id": pid,
            "workspace_id": workspace_id,
            "name": body.name,
            "instructions": body.instructions,
            "status": "draft",
            "review_required": body.review_required,
            "lock_timeout_hours": body.lock_timeout_hours,
            "task_boundary_type": None,
            "created_by": current_user.user_uuid,
            "created_by_name": current_user.user_name,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        if body.aoi is not None:
            self._aois[pid] = {"upcast_to": "MultiPolygon"}
        return self._response(self._projects[pid])

    async def list_projects(
        self,
        workspace_id: int,
        *,
        status_filter=None,
        text_search=None,
        page: int = 1,
        page_size: int = 20,
        order_by: str = "created_at",
        order_dir: str = "DESC",
    ):
        from api.src.tasking.projects.dtos import (
            Pagination,
            ProjectListItem,
            ProjectListResponse,
        )

        rows = [
            p
            for p in self._projects.values()
            if p["workspace_id"] == workspace_id and p["deleted_at"] is None
        ]
        if status_filter is not None:
            rows = [p for p in rows if p["status"] == status_filter]
        if text_search:
            t = text_search.lower()
            rows = [p for p in rows if t in p["name"].lower()]

        total = len(rows)
        offset = (page - 1) * page_size
        items = [
            ProjectListItem(
                id=p["id"],
                name=p["name"],
                status=p["status"],
                task_count=0,
                percent_completed=0,
                created_by=p["created_by"],
                created_by_name=p.get("created_by_name"),
                created_at=p["created_at"],
                updated_at=p["updated_at"],
            )
            for p in rows[offset : offset + page_size]
        ]
        return ProjectListResponse(
            results=items,
            pagination=Pagination(page=page, page_size=page_size, total=total),
        )

    async def get(self, workspace_id: int, project_id: int):
        from api.core.exceptions import NotFoundException

        p = self._projects.get(project_id)
        if p is None or p["workspace_id"] != workspace_id or p["deleted_at"]:
            raise NotFoundException(f"Project {project_id} not found")
        return self._response(p)

    async def project_name_exists(self, workspace_id: int, name: str) -> bool:
        return any(
            p["workspace_id"] == workspace_id
            and p["name"] == name
            and p["deleted_at"] is None
            for p in self._projects.values()
        )

    async def patch(self, workspace_id, project_id, body, current_user):
        p_resp = await self.get(workspace_id, project_id)
        p = self._projects[project_id]
        if body.name is not None:
            p["name"] = body.name.strip()
        if body.instructions is not None:
            p["instructions"] = body.instructions
        if body.lock_timeout_hours is not None:
            p["lock_timeout_hours"] = body.lock_timeout_hours
        if body.review_required is not None:
            p["review_required"] = body.review_required
        p["updated_at"] = datetime.now()
        return self._response(p)

    async def soft_delete(self, workspace_id, project_id, current_user):
        await self.get(workspace_id, project_id)
        self._projects[project_id]["deleted_at"] = datetime.now()

    async def activate(self, workspace_id, project_id, current_user):
        from fastapi import HTTPException, status

        # Mirrors the real repo's "needs tasks" rule.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Project must have at least one task",
        )

    async def close(self, workspace_id, project_id, current_user):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only open projects can be closed",
        )

    async def reset(self, workspace_id, project_id, current_user):
        await self.get(workspace_id, project_id)
        return self._response(self._projects[project_id])

    # ---- AOI ------------------------------------------------------------

    async def get_aoi(self, workspace_id, project_id):
        from api.core.exceptions import NotFoundException
        from api.src.tasking.projects.dtos import AoiFeature
        from api.src.tasking.projects.schemas import _MultiPolygon

        await self.get(workspace_id, project_id)
        if project_id not in self._aois:
            raise NotFoundException("AOI is not set on this project")
        return AoiFeature(
            type="Feature",
            geometry=_MultiPolygon(
                type="MultiPolygon",
                coordinates=[[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]],
            ),
            properties={},
        )

    async def upload_aoi(self, workspace_id, project_id, aoi, current_user):
        from api.src.tasking.projects.dtos import AoiFeature
        from api.src.tasking.projects.schemas import _MultiPolygon

        await self.get(workspace_id, project_id)
        self._aois[project_id] = {"raw": aoi}
        return AoiFeature(
            type="Feature",
            geometry=_MultiPolygon(
                type="MultiPolygon",
                coordinates=[[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]],
            ),
            properties={},
        )

    async def delete_aoi(self, workspace_id, project_id, current_user):
        from api.core.exceptions import NotFoundException

        await self.get(workspace_id, project_id)
        if project_id not in self._aois:
            raise NotFoundException("AOI is not set on this project")
        del self._aois[project_id]


# ---------------------------------------------------------------------------
# Dependency override fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_project_repo():
    """Swap ``get_project_repo`` with an in-memory FakeProjectRepo."""
    from api.main import app
    from api.src.tasking.projects.routes import get_project_repo

    repo = FakeProjectRepo()
    app.dependency_overrides[get_project_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_project_repo, None)


@pytest.fixture
def fake_workspace_repo():
    """Swap ``get_workspace_repo`` with the in-memory FakeWorkspaceRepo."""
    from api.main import app
    from api.src.tasking.projects.routes import get_workspace_repo

    repo = FakeWorkspaceRepo()
    app.dependency_overrides[get_workspace_repo] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_workspace_repo, None)


# Convenience: most route tests need both. One fixture, request once.
@pytest.fixture
def fake_repos(fake_project_repo, fake_workspace_repo):
    return SimpleNamespace(
        project=fake_project_repo,
        workspace=fake_workspace_repo,
    )
