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

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.core.database import get_osm_session, get_task_session
from api.core.security import validate_token
from api.main import app as fastapi_app
from tests.support import factories
from tests.support.fakes import FakeSession


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
