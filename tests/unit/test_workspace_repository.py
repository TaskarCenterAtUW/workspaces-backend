"""Unit tests for WorkspaceRepository against a fake session.

These exercise the repository in isolation (no HTTP layer) to show how the
data-fetcher boundary is mocked: queue the rows the DB "would" return, then
assert on the repository's behavior and writes.
"""

from typing import cast
from uuid import UUID

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import ForbiddenException, NotFoundException
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.schemas import WorkspaceCreate, WorkspaceType
from tests.support import factories, fakes


def _repo(session: fakes.FakeSession) -> WorkspaceRepository:
    # FakeSession implements the AsyncSession surface the repository uses.
    return WorkspaceRepository(cast(AsyncSession, session))


@pytest.fixture
def user():
    return factories.make_user_info()


async def test_get_by_id_returns_workspace(user):
    workspace = factories.make_workspace(id=1, title="Mapping WS")
    session = fakes.FakeSession(fakes.rows(workspace))

    result = await _repo(session).getById(user, 1)

    assert result.id == 1
    assert result.title == "Mapping WS"


async def test_get_by_id_missing_raises_not_found(user):
    session = fakes.FakeSession(fakes.empty())

    with pytest.raises(NotFoundException):
        await _repo(session).getById(user, 404)


async def test_get_all_returns_list(user):
    session = fakes.FakeSession(
        fakes.rows(factories.make_workspace(id=1), factories.make_workspace(id=2))
    )

    result = await _repo(session).getAll(user)

    assert [w.id for w in result] == [1, 2]


async def test_create_commits_and_assigns_id(user):
    session = fakes.FakeSession()

    workspace = await _repo(session).create(
        user,
        WorkspaceCreate(
            type=WorkspaceType.OSW,
            title="Brand New",
            tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
        ),
    )

    assert session.commits == 1
    assert workspace in session.added
    assert workspace.id is not None  # assigned by refresh()
    assert workspace.createdByName == "Test User"


async def test_create_in_unauthorized_group_raises(user):
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(
            user,
            WorkspaceCreate(
                type=WorkspaceType.OSW,
                title="Forbidden",
                tdeiProjectGroupId=UUID("99999999-9999-9999-9999-999999999999"),
            ),
        )
    assert session.commits == 0


async def test_delete_missing_raises_not_found(user):
    session = fakes.FakeSession(fakes.affected(0))

    with pytest.raises(NotFoundException):
        await _repo(session).delete(user, 1)
