"""Unit tests for JobRepository.update against a fake session.

See tests/unit/test_user_repository.py for the pattern this follows.
"""

from datetime import datetime
from typing import cast

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from api.core.exceptions import NotFoundException
from api.src.workspaces.jobs.repository import JobRepository
from api.src.workspaces.jobs.schemas import Job, JobPatch
from tests.support import fakes
from tests.support.factories import make_user_info


def _repo(session: fakes.FakeSession) -> JobRepository:
    return JobRepository(cast(AsyncSession, session))


def _job(
    id: int = 1,
    workspace_id: int = 5,
    request: dict | None = None,
    status: str = "requested",
) -> Job:
    now = datetime.now()
    return Job(
        id=id,
        job_type="workspace-import",
        status=status,
        request=request or {},
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )


async def test_update_ignore_permissions_updates_fields_regardless_of_workspace():
    job = _job(id=145, workspace_id=999)
    session = fakes.FakeSession(get_results=[job])

    result = await _repo(session).update(
        make_user_info(accessible_workspace_ids={}),
        145,
        JobPatch(status="completed"),
        ignore_permissions=True,
    )

    assert result.status == "completed"
    assert session.commits == 1


async def test_update_ignore_permissions_raises_not_found_when_missing():
    session = fakes.FakeSession(get_results=[None])

    with pytest.raises(NotFoundException):
        await _repo(session).update(
            make_user_info(),
            999,
            JobPatch(status="completed"),
            ignore_permissions=True,
        )


async def test_update_allows_access_when_workspace_is_accessible():
    job = _job(id=1, workspace_id=5)
    session = fakes.FakeSession(get_results=[job])
    user = make_user_info(accessible_workspace_ids={"pg": [5]})

    result = await _repo(session).update(user, 1, JobPatch(status="completed"))

    assert result.status == "completed"


async def test_update_raises_not_found_when_workspace_not_accessible():
    job = _job(id=1, workspace_id=5)
    session = fakes.FakeSession(get_results=[job])
    user = make_user_info(accessible_workspace_ids={"pg": [999]})

    with pytest.raises(NotFoundException):
        await _repo(session).update(user, 1, JobPatch(status="completed"))


async def test_update_raises_not_found_when_job_missing_and_not_ignoring_permissions():
    session = fakes.FakeSession(get_results=[None])
    user = make_user_info(accessible_workspace_ids={"pg": [5]})

    with pytest.raises(NotFoundException):
        await _repo(session).update(user, 999, JobPatch(status="completed"))


async def test_update_only_applies_fields_set_on_patch():
    job = _job(id=1, workspace_id=5, request={"a": 1})
    session = fakes.FakeSession(get_results=[job])
    user = make_user_info(accessible_workspace_ids={"pg": [5]})

    result = await _repo(session).update(user, 1, JobPatch(status="completed"))

    assert result.status == "completed"
    assert result.request == {"a": 1}
