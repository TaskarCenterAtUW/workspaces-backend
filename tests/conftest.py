"""Shared pytest fixtures.

Integration tests drive the *real* FastAPI app through an ASGI HTTP client,
overriding only three dependencies:

* ``get_task_session`` / ``get_osm_session`` -> :class:`FakeSession` (the
  "data fetcher" boundary; queue simulated rows per test).
* ``validate_token`` -> a real ``UserInfo`` built by the factories (skips
  JWT decoding and the TDEI network call, but the permission logic is real).

Everything above that boundary -- routes, repositories, schemas, Pydantic
serialization -- runs unmodified.
"""

from collections.abc import Iterator
from typing import Callable
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.core.database import get_osm_session, get_task_session
from api.core.security import validate_token
from api.main import app as fastapi_app
from tests.support import factories
from tests.support.fakes import FakeSession

# Shared with the testcontainers-backed integration suite
# (``tests/integration/conftest.py``), which imports these to seed a real
# workspaces row and to synthesize ``UserInfo`` principals. Kept here so both
# the FakeSession-based and testcontainers-based test layers reference one
# canonical workspace/project-group identity.
SEED_WORKSPACE_ID = 1899
SEED_PROJECT_GROUP_ID = UUID("00000000-0000-0000-0000-000000001899")


def _make_user(
    *,
    role: str | None,
    workspace_id: int,
    pg_id: UUID,
    is_poc: bool = False,
):
    """Construct a UserInfo with the minimum fields the gates inspect."""
    from api.core.security import TdeiProjectGroupRole, UserInfo, UserInfoPGMembership
    from api.src.users.schemas import WorkspaceUserRoleType

    u = UserInfo()
    u.credentials = "fake-token"
    u.user_uuid = uuid4()
    u.user_name = f"test-{role or 'outsider'}-{u.user_uuid.hex[:6]}"

    if role == "lead":
        u.osmWorkspaceRoles = {workspace_id: [WorkspaceUserRoleType.LEAD]}
    elif role == "validator":
        u.osmWorkspaceRoles = {workspace_id: [WorkspaceUserRoleType.VALIDATOR]}
    elif role == "contributor":
        u.osmWorkspaceRoles = {workspace_id: [WorkspaceUserRoleType.CONTRIBUTOR]}
    else:
        u.osmWorkspaceRoles = {}

    pg_roles = [TdeiProjectGroupRole.MEMBER]
    if is_poc:
        pg_roles.append(TdeiProjectGroupRole.POINT_OF_CONTACT)

    # Outsiders belong to no project group at all -> 404 on tenancy gate.
    if role is None and not is_poc:
        u.projectGroups = []
        u.accessibleWorkspaceIds = {}
    else:
        u.projectGroups = [
            UserInfoPGMembership(
                project_group_name="Test PG",
                project_group_id=str(pg_id),
                tdeiRoles=pg_roles,
            )
        ]
        u.accessibleWorkspaceIds = {str(pg_id): [workspace_id]}

    return u


@pytest.fixture
def task_session() -> FakeSession:
    """Fake session backing the tasking-manager / workspaces DB."""
    return FakeSession()


@pytest.fixture
def osm_session() -> FakeSession:
    """Fake session backing the OSM DB."""
    return FakeSession()


@pytest.fixture
def app(task_session, osm_session):
    """The real app with its DB sessions overridden by fakes."""
    fastapi_app.dependency_overrides[get_task_session] = lambda: task_session
    fastapi_app.dependency_overrides[get_osm_session] = lambda: osm_session
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def login(app):
    """Authenticate the request as a given user.

    Call with a ``UserInfo`` (see ``factories.make_user_info``) to set the
    authenticated principal; called with no args it logs in a default user.
    Returns the ``UserInfo`` in effect.
    """

    def _login(user_info=None):
        user_info = user_info or factories.make_user_info()
        app.dependency_overrides[validate_token] = lambda: user_info
        return user_info

    return _login


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP client bound to the app over an in-process ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def error_client(app):
    """Like ``client`` but turns unhandled exceptions into 500 responses.

    By default httpx's ASGI transport re-raises app exceptions; this fixture
    lets tests assert on the 500 the server would actually return in prod.
    """
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Sync auth fixtures for the FakeSession/repository-level unit suite.
# The testcontainers integration layer overrides these async in
# ``tests/integration/conftest.py`` to seed real rows; unit tests use the
# lightweight versions below.
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_workspace_id() -> int:
    """Workspace id used by route URLs.

    Unit tests pretend this exists via a FakeWorkspaceRepository.
    Integration overrides this fixture (and seeds a real workspaces
    row with the same id) in ``tests/integration/conftest.py``.
    """
    return SEED_WORKSPACE_ID


@pytest.fixture
def override_user() -> Iterator[Callable]:
    """Yields a setter that swaps `validate_token` for the duration of a test."""
    from api.core.security import validate_token
    from api.main import app

    def _set(user) -> None:
        app.dependency_overrides[validate_token] = lambda: user

    yield _set
    app.dependency_overrides.pop(validate_token, None)


@pytest.fixture
def as_lead(override_user, seeded_workspace_id):
    user = _make_user(
        role="lead",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    override_user(user)
    return user


@pytest.fixture
def as_contributor(override_user, seeded_workspace_id):
    user = _make_user(
        role="contributor",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    override_user(user)
    return user


@pytest.fixture
def as_validator(override_user, seeded_workspace_id):
    user = _make_user(
        role="validator",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    override_user(user)
    return user


@pytest.fixture
def as_outsider(override_user, seeded_workspace_id):
    """User with no project-group association — tenancy gate should 404."""
    user = _make_user(
        role=None,
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    override_user(user)
    return user
