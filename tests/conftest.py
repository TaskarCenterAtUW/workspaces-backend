
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Callable
from uuid import UUID, uuid4

import pytest


# Constants — referenced by both unit and integration suites.
SEED_WORKSPACE_ID = 1899
SEED_PROJECT_GROUP_ID = UUID("00000000-0000-0000-0000-000000001899")


@pytest.fixture
def seeded_workspace_id() -> int:
    """Workspace id used by route URLs.

    Unit tests pretend this exists via a FakeWorkspaceRepository.
    Integration overrides this fixture (and seeds a real workspaces
    row with the same id) in ``tests/integration/conftest.py``.
    """
    return SEED_WORKSPACE_ID


# ---------------------------------------------------------------------------
# HTTP client — ASGI transport (no socket); shared by unit and
# integration suites. The `request` / `response` event hooks log every
# call through stdlib `logging` so pytest-html captures the full HTTP
# trace per test in the report.
# ---------------------------------------------------------------------------


import logging  # noqa: E402  (kept near the fixture for locality)

_http_log = logging.getLogger("tests.http")


# ---------------------------------------------------------------------------
# pytest-html: surface each test's docstring as a "Description" column
# so the report is self-explanatory.
# ---------------------------------------------------------------------------


def _test_description(item) -> str:
    """Pull the first non-empty docstring line off a pytest item."""
    fn = getattr(item, "function", None) or getattr(item, "obj", None)
    doc = (getattr(fn, "__doc__", None) or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Annotate the test report with the docstring for pytest-html to render."""
    outcome = yield
    report = outcome.get_result()
    report.description = _test_description(item)


# The two hooks below are owned by the `pytest-html` plugin. When the
# plugin isn't installed (e.g. `uv sync` doesn't include it as a base
# dependency), pluggy refuses to register them and aborts collection.
# Declare them conditionally so the suite runs either way.
try:
    import pytest_html  # noqa: F401

    def pytest_html_results_table_header(cells):
        """Inject a 'Description' column header next to the test name."""
        cells.insert(2, "<th>Description</th>")

    def pytest_html_results_table_row(report, cells):
        """Inject the docstring as the matching cell on each row."""
        cells.insert(
            2, f"<td>{getattr(report, 'description', '') or '—'}</td>"
        )
except ImportError:
    pass


def _redact(headers) -> str:
    redact = {"authorization", "cookie", "set-cookie", "x-api-key"}
    return ", ".join(
        f"{k}={'***' if k.lower() in redact else v}"
        for k, v in headers.items()
    )


@pytest.fixture
async def client() -> AsyncIterator:
    from httpx import ASGITransport, AsyncClient

    from api.main import app

    async def _on_request(request):
        body_preview = ""
        try:
            if request.content:
                raw = request.content.decode(errors="replace")
                body_preview = f"  body={raw[:500]}{'…' if len(raw) > 500 else ''}"
        except Exception:
            pass
        _http_log.info(
            "→ %s %s%s%s",
            request.method,
            request.url.path,
            f"?{request.url.query.decode()}" if request.url.query else "",
            body_preview,
        )

    async def _on_response(response):
        try:
            await response.aread()
        except Exception:
            pass
        body_preview = ""
        if response.content:
            try:
                raw = response.content.decode(errors="replace")
                body_preview = f"  body={raw[:500]}{'…' if len(raw) > 500 else ''}"
            except Exception:
                pass
        _http_log.info(
            "← %s %s %s  (%dB)%s",
            response.status_code,
            response.request.method,
            response.request.url.path,
            len(response.content),
            body_preview,
        )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        event_hooks={"request": [_on_request], "response": [_on_response]},
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Fake users — bypass TDEI/Keycloak by overriding `validate_token`.
# ---------------------------------------------------------------------------


def _make_user(
    *,
    role: str | None,
    workspace_id: int,
    pg_id: UUID,
    is_poc: bool = False,
):
    """Construct a UserInfo with the minimum fields the gates inspect."""
    from api.core.security import (
        TdeiProjectGroupRole,
        UserInfo,
        UserInfoPGMembership,
    )
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
