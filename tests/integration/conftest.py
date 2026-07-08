from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest

from tests.conftest import SEED_PROJECT_GROUP_ID, SEED_WORKSPACE_ID

# ---------------------------------------------------------------------------
# Docker availability gate — skip cleanly when the daemon is missing
# and surface the actual reason in the pytest skip message.
# ---------------------------------------------------------------------------


def _docker_status() -> tuple[bool, str]:
    """Returns (ok, reason). 'reason' is empty when ok, otherwise diagnostic text."""
    try:
        r = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return False, (
            "`docker` CLI not on PATH for subprocess. "
            "If you use colima/OrbStack/Rancher Desktop, confirm the docker "
            "binary is in /usr/local/bin or symlinked there."
        )
    except subprocess.TimeoutExpired:
        return False, (
            "`docker info` timed out after 10s — daemon is probably not "
            "running. Start Docker Desktop (or your runtime of choice)."
        )

    if r.returncode == 0:
        return True, ""

    err = (r.stderr or r.stdout or "").strip().splitlines()
    # Keep the skip message readable: first non-empty line is usually enough.
    first = next((ln for ln in err if ln.strip()), "")
    return False, f"`docker info` exit {r.returncode}: {first[:200]}"


_DOCKER_OK, _DOCKER_REASON = _docker_status()


# ---------------------------------------------------------------------------
# PostGIS container.
#
# Booted in `pytest_configure` so the TASK_DATABASE_URL and
# OSM_DATABASE_URL environment variables are set before any test file
# imports `api.*` — the engines in `api.core.database` are constructed
# at module load and would otherwise bind to the URLs from `.env`.
#
# Each alembic tree runs against its own database to avoid colliding
# on the default `alembic_version` table:
#   - default container DB → TASK_DATABASE_URL (workspaces, workspaces_*)
#   - provisioned `/osm_test` → OSM_DATABASE_URL (users, teams, tasking_*)
# ---------------------------------------------------------------------------


async def _provision_osm_db(task_url: str, db_name: str) -> str:
    """Create an additional database on the same Postgres instance.

    Returns the connection URL for the new DB. Uses AUTOCOMMIT isolation so
    ``CREATE DATABASE`` doesn't error inside a transaction.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(task_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            )
            if result.scalar() is None:
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()
    return task_url.rsplit("/", 1)[0] + "/" + db_name


# Module-level state owned by pytest_configure / pytest_unconfigure.
_INTEGRATION_CONTAINER = None


def _wants_integration(config) -> bool:
    """Inspect the markexpr to decide whether to boot the container this run."""
    m = (config.option.markexpr or "").strip()
    if not m:
        # Bare `pytest` runs with `addopts = -m 'not integration'`, so m
        # will be 'not integration' — handled below. Defensive default.
        return False
    if m == "not integration":
        return False
    # Anything that mentions integration positively triggers a boot.
    return "integration" in m


def pytest_configure(config):
    """Boot the testcontainer BEFORE test files are imported (collection phase).

    This sets ``TASK_DATABASE_URL`` and ``OSM_DATABASE_URL`` in ``os.environ``
    so that ``api.core.config.settings`` — read at module load by
    ``api.core.database`` to build engines — picks up the container URLs.
    """
    global _INTEGRATION_CONTAINER

    if not _wants_integration(config):
        return
    if not _DOCKER_OK:
        return  # let the fixture surface the skip with a clean reason
    try:
        from testcontainers.postgres import (  # pyright: ignore[reportMissingImports]
            PostgresContainer,
        )
    except ImportError:
        return  # ditto — fixture-time skip with install instructions

    import asyncio

    container = PostgresContainer("postgis/postgis:16-3.4", driver="asyncpg")
    container.start()
    task_url = container.get_connection_url()
    osm_url = asyncio.run(_provision_osm_db(task_url, "osm_test"))

    os.environ["TASK_DATABASE_URL"] = task_url
    os.environ["OSM_DATABASE_URL"] = osm_url
    _INTEGRATION_CONTAINER = container


def pytest_unconfigure(config):
    """Tear the container down at the end of the pytest session."""
    global _INTEGRATION_CONTAINER
    if _INTEGRATION_CONTAINER is not None:
        try:
            _INTEGRATION_CONTAINER.stop()
        finally:
            _INTEGRATION_CONTAINER = None


@pytest.fixture(scope="session")
def _pg_urls() -> Iterator[tuple[str, str]]:
    """Yield (task_url, osm_url) populated by ``pytest_configure``."""
    if not _DOCKER_OK:
        pytest.skip(f"Docker not available — {_DOCKER_REASON}")
    try:
        import testcontainers.postgres  # noqa: F401  # pyright: ignore[reportMissingImports]
    except ImportError:
        pytest.skip(
            "testcontainers not installed; install with "
            "`uv sync --extra integration`"
        )

    task_url = os.environ.get("TASK_DATABASE_URL")
    osm_url = os.environ.get("OSM_DATABASE_URL")
    if not task_url or not osm_url or _INTEGRATION_CONTAINER is None:
        pytest.skip(
            "Integration container is not running — invoke pytest with "
            "`-m integration` so pytest_configure can boot it."
        )
    yield task_url, osm_url


# Backwards-compatible alias for fixtures that only need one URL (the
# tasking_* tables live in the OSM database).
@pytest.fixture(scope="session")
def _pg_url(_pg_urls: tuple[str, str]) -> str:
    return _pg_urls[1]


# ---------------------------------------------------------------------------
# Alembic upgrade — each tree against its own database.
# ---------------------------------------------------------------------------


async def _bootstrap_osm_database(osm_url: str) -> None:
    """Prepare the provisioned OSM database for the osm alembic tree.

    Two things the tree assumes but does not create itself:

    * **PostGIS** — the tasking_* migration requires the extension and raises
      if it is missing. The container image ships PostGIS but the extension
      must be enabled per-database, and the freshly ``CREATE DATABASE``'d
      ``osm_test`` doesn't inherit it.
    * **users** — owned by the OSM Rails website, not these alembic trees:
      migration ``9221408912dd`` adds a unique constraint to it and the
      ``tasking_*`` foreign keys reference ``users.id`` / ``users.auth_uid``,
      all assuming it already exists. We stand up a stripped-down version
      (just the columns the migrations and ``_insert_user_row`` touch).
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(osm_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.execute(
                text(
                    # Columns mirror the subset of the OSM Rails `users` table
                    # that the tasking code reads or writes: the FK targets
                    # (id, auth_uid), the seed/display columns, and every column
                    # the TDEI auto-provisioning INSERT populates
                    # (see TaskingProjectRepository._provision_users_from_tdei).
                    "CREATE TABLE IF NOT EXISTS users ("
                    "  id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,"
                    "  auth_uid varchar,"
                    "  email varchar,"
                    "  display_name varchar,"
                    "  auth_provider varchar,"
                    "  status varchar,"
                    "  pass_crypt varchar,"
                    "  data_public boolean,"
                    "  email_valid boolean,"
                    "  terms_seen boolean,"
                    "  creation_time timestamp,"
                    "  terms_agreed timestamp,"
                    "  tou_agreed timestamp"
                    ")"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def _migrated_db(_pg_urls: tuple[str, str]) -> tuple[str, str]:
    """Run alembic upgrades for both trees against their own databases.

    Returns ``(task_url, osm_url)`` after both heads are reached.
    """
    import asyncio

    task_url, osm_url = _pg_urls

    # PostGIS + the OSM-website-owned `users` table must exist before the osm
    # alembic tree upgrades (its migrations require the extension and
    # constrain `users.auth_uid`).
    asyncio.run(_bootstrap_osm_database(osm_url))

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    env = os.environ.copy()
    env["TASK_DATABASE_URL"] = task_url
    env["OSM_DATABASE_URL"] = osm_url
    for db_name in ("task", "osm"):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-n", db_name, "upgrade", "head"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            env=env,
        )
        if result.returncode != 0:
            pytest.fail(
                f"alembic '{db_name}' upgrade failed:\n"
                f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
            )
    return task_url, osm_url


# ---------------------------------------------------------------------------
# Seed: one workspace row matching SEED_WORKSPACE_ID.
# ---------------------------------------------------------------------------


async def _insert_workspace_row(task_url: str) -> None:
    """Insert the seed workspace row into the TASK database."""
    from uuid import uuid4

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(task_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO workspaces "
                    '(id, type, title, "tdeiProjectGroupId", "createdAt", '
                    ' "createdBy", "createdByName", "externalAppAccess") '
                    "VALUES (:id, :type, :title, :pgid, NOW(), :uid, :uname, 0) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {
                    "id": SEED_WORKSPACE_ID,
                    "type": "osw",
                    "title": "Test Workspace",
                    "pgid": str(SEED_PROJECT_GROUP_ID),
                    "uid": str(uuid4()),
                    "uname": "seed",
                },
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def _seed_workspace_row(_migrated_db: tuple[str, str]) -> int:
    """Insert one workspace row so tenancy/permission gates resolve.

    Workspaces live in the TASK database (alongside teams, project groups),
    so we point this at task_url. Kept synchronous (driving the async insert
    via ``asyncio.run``) because a session-scoped *async* fixture would clash
    with pytest-asyncio's function-scoped event loop.
    """
    import asyncio

    task_url, _osm_url = _migrated_db
    asyncio.run(_insert_workspace_row(task_url))
    return SEED_WORKSPACE_ID


# Override the shared `seeded_workspace_id` fixture so that, inside the
# integration suite, requesting it triggers the testcontainer + migration
# + seed pipeline. Unit tests get the bare constant from tests/conftest.py.
@pytest.fixture
async def seeded_workspace_id(_seed_workspace_row: int) -> int:
    return _seed_workspace_row


# ---------------------------------------------------------------------------
# User-row seeding — `tasking_project_roles.user_auth_uid` has a FK to
# `users.auth_uid`, so every fake user that hits the DB needs a matching
# row. Override the shared auth fixtures here to handle that.
# ---------------------------------------------------------------------------


async def _insert_user_row(osm_url: str, user) -> None:
    """Insert a row into ``users`` matching a fabricated UserInfo."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (auth_uid, email, display_name) "
                    "VALUES (:uid, :email, :name) "
                    "ON CONFLICT (auth_uid) DO NOTHING"
                ),
                {
                    "uid": str(user.user_uuid),
                    "email": f"{user.user_uuid}@test.local",
                    "name": user.user_name,
                },
            )
    finally:
        await engine.dispose()


def _override_token(user) -> None:
    from api.core.security import validate_token
    from api.main import app

    app.dependency_overrides[validate_token] = lambda: user


def _clear_token() -> None:
    from api.core.security import validate_token
    from api.main import app

    app.dependency_overrides.pop(validate_token, None)


# ---------------------------------------------------------------------------
# Per-test DB session override.
#
# `api/core/database.py` constructs its asyncpg engines at module load,
# binding their pool connections to the event loop active at that
# moment. pytest-asyncio uses a fresh event loop per function-scoped
# test, so a shared engine would carry stale connections across loops
# and asyncpg would raise ``InterfaceError: cannot perform operation:
# another operation is in progress``.
#
# The autouse fixture below replaces `get_task_session` and
# `get_osm_session` with per-test engines (NullPool, disposed on
# teardown), so each test opens its connections on its own loop.
# ---------------------------------------------------------------------------


@pytest.fixture
def app(request, task_session, osm_session):
    """App fixture for everything under ``tests/integration/``.

    This directory hosts two kinds of test that share one conftest:

    * **Container-backed** tests (marked ``integration``) run against a real
      PostGIS database; their DB sessions are the real engines wired by the
      autouse ``_per_test_db_sessions`` fixture, so we hand back the app
      untouched here.
    * **FakeSession-backed** tests (unmarked — the CLAUDE.md data-fetcher
      model) run the real routes/repositories but fake the ``AsyncSession``.
      For those we wire the fakes in exactly like the unit-suite ``app``
      fixture this shadows.

    Keying on the ``integration`` marker keeps the container machinery from
    leaking onto the FakeSession tests (which need no Docker).
    """
    from api.core.database import get_osm_session, get_task_session
    from api.main import app as fastapi_app

    if request.node.get_closest_marker("integration"):
        yield fastapi_app
        return

    fastapi_app.dependency_overrides[get_task_session] = lambda: task_session
    fastapi_app.dependency_overrides[get_osm_session] = lambda: osm_session
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def _per_test_db_sessions(request):
    # Only container-backed (``integration``-marked) tests need real DB
    # sessions; for FakeSession tests this fixture is a no-op so they never
    # pull in ``_pg_urls`` (which would boot / require the testcontainer).
    if not request.node.get_closest_marker("integration"):
        yield
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlmodel.ext.asyncio.session import AsyncSession

    from api.core.database import get_osm_session, get_task_session
    from api.main import app

    task_url, osm_url = request.getfixturevalue("_pg_urls")

    # NullPool disables connection reuse: each session checkout opens a
    # fresh connection on the current event loop and disposes it on
    # exit, preventing cross-loop pooled-connection errors.
    task_engine = create_async_engine(task_url, future=True, poolclass=NullPool)
    osm_engine = create_async_engine(osm_url, future=True, poolclass=NullPool)

    task_factory = async_sessionmaker(
        bind=task_engine, class_=AsyncSession, expire_on_commit=False
    )
    osm_factory = async_sessionmaker(
        bind=osm_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _get_task():
        async with task_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    async def _get_osm():
        async with osm_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_task_session] = _get_task
    app.dependency_overrides[get_osm_session] = _get_osm

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_task_session, None)
        app.dependency_overrides.pop(get_osm_session, None)
        await task_engine.dispose()
        await osm_engine.dispose()


# Role fixtures — each inserts a matching `users` row and overrides
# `validate_token`. These shadow the unit-suite counterparts in
# tests/conftest.py for every test under tests/integration/.


@pytest.fixture
async def as_lead(_pg_urls, seeded_workspace_id):
    """LEAD user persisted in users table + overridden in validate_token."""
    from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

    _, osm_url = _pg_urls
    user = _make_user(
        role="lead",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    await _insert_user_row(osm_url, user)
    _override_token(user)
    yield user
    _clear_token()


@pytest.fixture
async def as_contributor(_pg_urls, seeded_workspace_id):
    """CONTRIBUTOR user persisted in users table + overridden in validate_token."""
    from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

    _, osm_url = _pg_urls
    user = _make_user(
        role="contributor",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    await _insert_user_row(osm_url, user)
    _override_token(user)
    yield user
    _clear_token()


@pytest.fixture
async def as_validator(_pg_urls, seeded_workspace_id):
    """VALIDATOR user persisted in users table + overridden in validate_token."""
    from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

    _, osm_url = _pg_urls
    user = _make_user(
        role="validator",
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    await _insert_user_row(osm_url, user)
    _override_token(user)
    yield user
    _clear_token()


@pytest.fixture
async def as_outsider(_pg_urls, seeded_workspace_id):
    """Outsider — no PG membership.

    Inserted into users so role tests don't break, but their workspace
    role list is empty so the tenancy gate still 404s.
    """
    from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

    _, osm_url = _pg_urls
    user = _make_user(
        role=None,
        workspace_id=seeded_workspace_id,
        pg_id=SEED_PROJECT_GROUP_ID,
    )
    await _insert_user_row(osm_url, user)
    _override_token(user)
    yield user
    _clear_token()


# ---------------------------------------------------------------------------
# Extra-user factory — for tests that need additional users beyond the
# single "active" caller. Inserts the matching `users` row so FKs hold,
# but does NOT swap `validate_token`. Tests that want to act-as the new
# user should pair this with the shared `override_user` fixture.
# ---------------------------------------------------------------------------


@pytest.fixture
async def extra_user_factory(_pg_urls, seeded_workspace_id):
    """Factory: ``await make_user("contributor")`` returns a fresh UserInfo
    persisted in the OSM `users` table.

    The caller's `validate_token` override is left untouched — pair with
    the `override_user` fixture from ``tests/conftest.py`` to act as the
    returned user.
    """
    from tests.conftest import SEED_PROJECT_GROUP_ID, _make_user

    _, osm_url = _pg_urls

    async def _make(role: str | None):
        user = _make_user(
            role=role,
            workspace_id=seeded_workspace_id,
            pg_id=SEED_PROJECT_GROUP_ID,
        )
        await _insert_user_row(osm_url, user)
        return user

    return _make


# ---------------------------------------------------------------------------
# TDEI stub — no real TDEI backend is available in integration. The default
# stub returns an empty member list, so any role_assignments[].user_id that
# is not already in `users` stays missing and surfaces as 422.
#
# Tests that exercise the auto-provisioning path append to
# `tdei_project_group_users` to simulate TDEI returning specific members.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tdei_project_group_users(monkeypatch):
    """Stub `fetch_project_group_users` to return a controllable list."""
    members: list = []

    async def _fake_fetch(project_group_id: str, bearer_token: str):
        return list(members)

    import api.core.security
    import api.src.tasking.projects.repository as proj_repo

    monkeypatch.setattr(api.core.security, "fetch_project_group_users", _fake_fetch)
    # The repository imports the symbol locally inside the helper, but be
    # belt-and-braces in case that ever changes:
    if hasattr(proj_repo, "fetch_project_group_users"):
        monkeypatch.setattr(proj_repo, "fetch_project_group_users", _fake_fetch)
    return members


# ---------------------------------------------------------------------------
# Per-test cleanup of tasking_* tables (opt-in).
# ---------------------------------------------------------------------------


@pytest.fixture
async def reset_tasking(_pg_url: str) -> AsyncIterator[None]:
    """Truncate tasking_* tables between tests that opt in.

    Workflow tests inside a class share state by design (class-scoped
    project id passed between steps), so this fixture is *opt-in* —
    only request it from tests that need a clean slate.
    """
    yield
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_pg_url, future=True)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE "
                "  tasking_audit_events, tasking_feedback, "
                "  tasking_task_save_idempotency, tasking_locks, "
                "  tasking_tasks, tasking_project_roles, tasking_projects "
                "RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()
