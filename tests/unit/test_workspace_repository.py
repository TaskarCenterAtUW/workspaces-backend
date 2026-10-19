"""Unit tests for WorkspaceRepository against a fake session.

These exercise the repository in isolation (no HTTP layer) to show how the
data-fetcher boundary is mocked: queue the rows the DB "would" return, then
assert on the repository's behavior and writes.
"""

from typing import cast
from uuid import UUID

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

import api.src.workspaces.repository as ws_repo
from api.core.exceptions import ForbiddenException, NotFoundException
from api.core.security import TdeiDataset, TdeiProjectGroupRole
from api.src.workspaces.repository import WorkspaceRepository
from api.src.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceCreateCheck,
    WorkspaceNameCheck,
    WorkspaceType,
)
from tests.support import factories, fakes


def _repo(session: fakes.FakeSession) -> WorkspaceRepository:
    # FakeSession implements the AsyncSession surface the repository uses.
    return WorkspaceRepository(cast(AsyncSession, session))


@pytest.fixture
def user():
    return factories.make_user_info()


@pytest.fixture
def creator():
    # Creating a workspace needs a non-member TDEI role in the project group.
    return factories.make_user_info(
        tdei_roles=[TdeiProjectGroupRole.MEMBER, TdeiProjectGroupRole.OSW_GENERATOR]
    )


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


async def test_create_commits_and_assigns_id(creator):
    session = fakes.FakeSession()

    workspace = await _repo(session).create(
        creator,
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


async def test_create_as_member_only_raises(user):
    # "member" is the only TDEI role that cannot create workspaces.
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(
            user,
            WorkspaceCreate(
                type=WorkspaceType.OSW,
                title="Member Only",
                tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
            ),
        )
    assert session.commits == 0


async def test_create_type_not_covered_by_role_raises(creator):
    # The OSW generator role may only create OSW workspaces.
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(
            creator,
            WorkspaceCreate(
                type=WorkspaceType.PATHWAYS,
                title="Wrong Type",
                tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
            ),
        )
    assert session.commits == 0


async def test_create_in_unauthorized_group_raises(creator):
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(
            creator,
            WorkspaceCreate(
                type=WorkspaceType.OSW,
                title="Forbidden",
                tdeiProjectGroupId=UUID("99999999-9999-9999-9999-999999999999"),
            ),
        )
    assert session.commits == 0


RECORD_ID = "33333333-3333-3333-3333-333333333333"
OTHER_PG_ID = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def tdei_datasets(monkeypatch):
    """Stub the TDEI lookup; records which dataset ids were fetched."""
    datasets: dict[str, TdeiDataset] = {}
    fetched: list[str] = []

    async def _fake_fetch(dataset_id: str, _bearer_token: str):
        fetched.append(dataset_id)
        return datasets.get(dataset_id)

    monkeypatch.setattr(ws_repo, "fetch_tdei_dataset", _fake_fetch)
    return datasets, fetched


def _dataset(project_group_id: str, status: str) -> TdeiDataset:
    return TdeiDataset(
        tdei_dataset_id=RECORD_ID, project_group_id=project_group_id, status=status
    )


def _from_dataset() -> WorkspaceCreate:
    return WorkspaceCreate(
        type=WorkspaceType.OSW,
        title="From TDEI",
        tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
        tdeiRecordId=UUID(RECORD_ID),
    )


@pytest.mark.parametrize(
    "dataset_pg, status",
    [
        (factories.DEFAULT_PG_ID, "Pre-Release"),  # unreleased, same PG
        (factories.DEFAULT_PG_ID, "Publish"),  # released, same PG
        (OTHER_PG_ID, "Publish"),  # released, any PG
    ],
)
async def test_create_from_dataset_allowed(creator, tdei_datasets, dataset_pg, status):
    datasets, fetched = tdei_datasets
    datasets[RECORD_ID] = _dataset(dataset_pg, status)
    session = fakes.FakeSession()

    await _repo(session).create(creator, _from_dataset())

    assert fetched == [RECORD_ID]
    assert session.commits == 1


async def test_create_from_unreleased_dataset_in_other_group_raises(
    creator, tdei_datasets
):
    datasets, _ = tdei_datasets
    datasets[RECORD_ID] = _dataset(OTHER_PG_ID, "Pre-Release")
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(creator, _from_dataset())
    assert session.commits == 0


async def test_create_from_unknown_dataset_raises_not_found(creator, tdei_datasets):
    session = fakes.FakeSession()

    with pytest.raises(NotFoundException):
        await _repo(session).create(creator, _from_dataset())
    assert session.commits == 0


async def test_create_without_dataset_skips_tdei_lookup(creator, tdei_datasets):
    # Blank and from-file creation have no tdeiRecordId to check.
    _, fetched = tdei_datasets
    session = fakes.FakeSession()

    await _repo(session).create(
        creator,
        WorkspaceCreate(
            type=WorkspaceType.OSW,
            title="Blank",
            tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
        ),
    )

    assert fetched == []
    assert session.commits == 1


async def test_create_member_only_is_rejected_before_tdei_lookup(user, tdei_datasets):
    _, fetched = tdei_datasets
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).create(user, _from_dataset())
    assert fetched == []


def _check(record_id: str | None = RECORD_ID) -> WorkspaceCreateCheck:
    return WorkspaceCreateCheck(
        type=WorkspaceType.OSW,
        tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID),
        tdeiRecordId=UUID(record_id) if record_id else None,
    )


@pytest.mark.parametrize(
    "dataset_pg, status",
    [
        (factories.DEFAULT_PG_ID, "Pre-Release"),  # unreleased, same PG
        (OTHER_PG_ID, "Publish"),  # released, any PG
    ],
)
async def test_check_create_permission_allowed(
    creator, tdei_datasets, dataset_pg, status
):
    datasets, _ = tdei_datasets
    datasets[RECORD_ID] = _dataset(dataset_pg, status)
    session = fakes.FakeSession()

    result = await _repo(session).check_create_permission(creator, _check())

    assert result.canCreate is True
    assert result.reason is None
    assert session.commits == 0  # a check never writes


async def test_check_create_permission_without_dataset_skips_tdei_lookup(
    creator, tdei_datasets
):
    _, fetched = tdei_datasets

    result = await _repo(fakes.FakeSession()).check_create_permission(
        creator, _check(record_id=None)
    )

    assert result.canCreate is True
    assert fetched == []


async def test_check_create_permission_member_only_denied(user, tdei_datasets):
    _, fetched = tdei_datasets

    result = await _repo(fakes.FakeSession()).check_create_permission(user, _check())

    assert result.canCreate is False
    assert result.reason is not None and "type" in result.reason
    assert fetched == []


async def test_check_create_permission_unreleased_other_group_denied(
    creator, tdei_datasets
):
    datasets, _ = tdei_datasets
    datasets[RECORD_ID] = _dataset(OTHER_PG_ID, "Pre-Release")

    result = await _repo(fakes.FakeSession()).check_create_permission(creator, _check())

    assert result.canCreate is False
    assert result.reason is not None and "unreleased" in result.reason


async def test_check_create_permission_unknown_dataset_denied(creator, tdei_datasets):
    result = await _repo(fakes.FakeSession()).check_create_permission(creator, _check())

    assert result.canCreate is False
    assert result.reason is not None and "not found" in result.reason


async def test_name_is_available_when_not_found(user):
    session = fakes.FakeSession(fakes.scalar(0))

    available = await _repo(session).is_name_available(
        user,
        WorkspaceNameCheck(
            title="Brand New", tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID)
        ),
    )

    assert available is True


async def test_name_is_unavailable_when_found(user):
    session = fakes.FakeSession(fakes.scalar(1))

    available = await _repo(session).is_name_available(
        user,
        WorkspaceNameCheck(
            title="Existing", tdeiProjectGroupId=UUID(factories.DEFAULT_PG_ID)
        ),
    )

    assert available is False


async def test_name_check_in_unauthorized_group_raises(user):
    session = fakes.FakeSession()

    with pytest.raises(ForbiddenException):
        await _repo(session).is_name_available(
            user,
            WorkspaceNameCheck(
                title="Forbidden",
                tdeiProjectGroupId=UUID("99999999-9999-9999-9999-999999999999"),
            ),
        )


async def test_delete_missing_raises_not_found(user):
    session = fakes.FakeSession(fakes.affected(0))

    with pytest.raises(NotFoundException):
        await _repo(session).delete(user, 1)
