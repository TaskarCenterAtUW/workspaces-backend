"""Unit tests for TaskingProjectRepository against a fake session.

See tests/unit/test_workspace_repository.py for the pattern this follows:
queue the rows the DB "would" return, then assert on the repository's
behavior. TaskingProjectRepository runs on the OSM DB session, since
tasking_projects lives there (see CLAUDE.md).
"""

from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession

from api.src.tasking.projects.repository import TaskingProjectRepository
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
