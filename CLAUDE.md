# CLAUDE.md

Guidance for working in this repo. Focused on the test infrastructure and
conventions established for it; see `README.md` for app setup.

## Permission Structure

Project Group Admin ("POC")
* Superuser for the whole project group
* Implied by "poc" role in TDEI

Lead/Owner/Workspace Admin
* Admin-level access for a workspace
* Configures workspace settings and quest definitions
* Assigns users to workspace teams
* Ability to merge changes from other workspace
* Exports data to TDEI (with appropriate TDEI core roles)
* Granted by Workspaces setting.

Contributor/Data Generator
* Modifies workspace data--all modifications need validation
* Implied by membership in TDEI project group

Validator
* Modifies workspace data and approves changes from contributors
* Granted by Workspaces setting.

Viewer/Member/Everyone Else
* Read-only access to workspace data
* With express TDEI sign-up, the need for this access level diminishes greatly
* Granted by Workspaces setting.

## Testing

Two layers, both fast and dependency-free (no Postgres, PostGIS, Docker, or
network). `tests/README.md` has the full reference; the essentials:

* **Unit** (`tests/unit/`) — pure logic and individual classes (permission
  rules, schema/DTO behavior, a repository in isolation).
* **Integration** (`tests/integration/`) — real HTTP requests driven through
  the real FastAPI app: routing, auth wiring, repositories, serialization.

### The mocking boundary is the "data fetcher", not the repository

Integration tests run the **real** routes and repositories. Only three things
are swapped out, via `app.dependency_overrides` and a fake:

1. `get_task_session` / `get_osm_session` → a `FakeSession` (in
   `tests/support/fakes.py`) that returns pre-programmed `FakeResult`s instead
   of running SQL. This is the data-fetcher boundary: everything above the
   `AsyncSession` runs for real.
2. `validate_token` → a real `UserInfo` built by `tests/support/factories.py`
   (skips JWT decode + the TDEI call; the permission logic is still real).
3. `api.main._osm_client` → a streamable mock transport (proxy tests only;
   `tests/support/http.py`).

Because the mock is at the session level, **queue results in the order the
repository issues queries**. Routes that touch both DBs queue on both
`task_session` and `osm_session`. Builders: `rows()`, `empty()`, `affected(n)`,
`mappings()`, `scalar(v)`, and `raises(exc)` (drives 500 paths). The
`error_client` fixture turns unhandled exceptions into 500 responses (httpx's
ASGI transport re-raises by default).

### `@test:` comment outlines

Modules carry `# @test:` comments describing intended coverage. They are the
spec for the test suite; when adding behavior, add matching `@test:` lines and
tests. Treat the docstring/attribute comments as authoritative when they and
the code disagree — file a fix rather than silently matching the code.

### Known behavior discrepancy: read endpoints return 404, not 403

Several `@test:` comments on read endpoints (get workspace, list teams, quest
and imagery GETs) specify a **403** when the caller lacks access. The code
enforces access via `WorkspaceRepository.getById`, which raises **404
NotFound** when the workspace is missing *or* inaccessible — so "not a member"
currently surfaces as 404 on those routes. The tests assert the actual 404
behavior and flag this in their docstrings. If 403 is the intended contract,
that is a code change in the read routes, not a test change.

### SQLModel + Pyright

SQLModel declares columns as plain annotations (e.g. `id: int | None`) rather
than `Mapped[int]`, so Pyright reads `Column == value` as `bool` and flags
`where()`/`exec()`/`select()`/`selectinload` calls and `result.rowcount`. These
are framework false positives. The three repository modules carry a documented
file-level `# pyright: reportArgumentType=false, reportCallIssue=false,
reportAttributeAccessIssue=false` directive; other rules stay enabled so real
bugs still surface. Keep `api/` and `tests/` at zero Pyright errors.

### Alembic enum migrations

Postgres `ENUM` types must be created/dropped idempotently. Declare the enum
with `create_type=False` and manage it explicitly with
`enum.create(op.get_bind(), checkfirst=True)` / `enum.drop(..., checkfirst=True)`
so a migration is safe whether or not the type already exists (and never
double-creates it via implicit table DDL).

