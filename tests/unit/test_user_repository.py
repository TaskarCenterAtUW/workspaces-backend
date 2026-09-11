"""Unit tests for UserRepository against a fake session.

See tests/unit/test_workspace_repository.py for the pattern this follows:
queue the rows the DB "would" return, then assert on the repository's
behavior.
"""

from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession

from api.src.users.repository import UserRepository
from tests.support import fakes


def _repo(session: fakes.FakeSession) -> UserRepository:
    return UserRepository(cast(AsyncSession, session))


async def test_get_member_counts_returns_map():
    session = fakes.FakeSession(fakes.rows((1, 2), (2, 1)))

    result = await _repo(session).get_member_counts([1, 2])

    assert result == {1: 2, 2: 1}


async def test_get_member_counts_empty_ids_short_circuits():
    session = fakes.FakeSession(fakes.raises(RuntimeError("should not query")))

    result = await _repo(session).get_member_counts([])

    assert result == {}


async def test_get_member_counts_omits_ids_with_no_members():
    session = fakes.FakeSession(fakes.rows((1, 3)))

    result = await _repo(session).get_member_counts([1, 2])

    assert result == {1: 3}
    assert result.get(2, 0) == 0
