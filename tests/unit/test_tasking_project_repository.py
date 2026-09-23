"""Unit tests for TaskingProjectRepository against a fake session.

See tests/unit/test_workspace_repository.py for the pattern this follows:
queue the rows the DB "would" return, then assert on the repository's
behavior. TaskingProjectRepository runs on the OSM DB session, since
tasking_projects lives there (see CLAUDE.md).
"""

from typing import cast
from uuid import uuid4

from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.security import UserInfo
from api.src.tasking.projects.repository import TaskingProjectRepository
from api.src.tasking.projects.schemas import TaskingProject
from tests.support import fakes


def _repo(session: fakes.FakeSession) -> TaskingProjectRepository:
    return TaskingProjectRepository(cast(AsyncSession, session))


async def test_get_projects_counts_returns_map():
    session = fakes.FakeSession(fakes.rows((1, 3), (2, 1)))

    result = await _repo(session).get_projects_counts([1, 2])

    assert result == {1: 3, 2: 1}


async def test_get_projects_counts_empty_ids_short_circuits():
    # A queued exception proves the session is never touched for an empty id list.
    session = fakes.FakeSession(fakes.raises(RuntimeError("should not query")))

    result = await _repo(session).get_projects_counts([])

    assert result == {}


async def test_get_projects_counts_omits_ids_with_no_projects():
    session = fakes.FakeSession(fakes.rows((1, 2)))

    result = await _repo(session).get_projects_counts([1, 2])

    assert result == {1: 2}
    assert result.get(2, 0) == 0


async def test_activate_accepts_project_lead_role():
    project = TaskingProject(
        id=7,
        workspace_id=3,
        name="Lead-owned project",
        aoi=object(),
        created_by=uuid4(),
    )
    session = fakes.FakeSession(
        fakes.rows(project),
        fakes.scalar(1),
        fakes.scalar(1),
        fakes.affected(1),
        fakes.affected(1),
        record_statements=True,
    )
    user = UserInfo()
    user.user_uuid = project.created_by

    await _repo(session).activate(3, 7, user)

    role_query = next(
        statement
        for statement in session.statements
        if "FROM tasking_project_roles" in statement
    )
    assert "'lead'" in role_query
    assert any(
        statement.startswith("UPDATE tasking_projects")
        for statement in session.statements
    )
    assert session.commits == 1
