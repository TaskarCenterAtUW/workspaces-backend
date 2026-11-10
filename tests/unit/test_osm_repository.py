"""Unit tests for OSMRepository's workspace schema selection.

Each method points the session at a workspace's schema before querying it.
That must be scoped to the transaction (SET LOCAL): a session-level SET stays
on the pooled connection and leaks into whichever request uses it next, where
unqualified names such as `jobs` resolve to the workspace schema's empty
copies instead of `public`.
"""

from typing import cast

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from api.src.osm.repository import OSMRepository
from api.src.users.schemas import WorkspaceUserRoleType
from tests.support import factories, fakes


async def _bbox(repo):
    await repo.getWorkspaceBBox(7)


async def _adiff(repo):
    await repo.getChangesetAdiff(7, 99)


async def _fixmes(repo):
    await repo.getFixmes(7)


async def _resolve(repo):
    user = factories.make_user_info(
        osm_workspace_roles={7: [WorkspaceUserRoleType.LEAD]}
    )
    await repo.resolveChangeset(user, 7, 99)


@pytest.mark.parametrize("call", [_bbox, _adiff, _fixmes, _resolve])
async def test_workspace_schema_is_scoped_to_the_transaction(call):
    session = fakes.FakeSession(
        fakes.mappings({"max_lat": 1, "max_lon": 1, "min_lat": 0, "min_lon": 0}),
        fakes.affected(1),
    )

    await call(OSMRepository(cast(AsyncSession, session)))

    assert session.setup_statements == [
        "SET LOCAL search_path TO 'workspace-7', public"
    ]


async def test_get_fixmes_returns_the_rows_and_matches_the_key_in_any_case():
    row = {"type": "node", "id": 5, "key": "FIXME", "note": "check the level"}
    session = fakes.FakeSession(fakes.mappings(row), record_statements=True)

    rows = await OSMRepository(cast(AsyncSession, session)).getFixmes(7)

    assert rows == [row]
    query = " ".join(str(s) for s in session.statements[-1:])
    assert "lower(k) = 'fixme'" in query
    assert "current_way_nodes" in query  # ways located from their nodes
