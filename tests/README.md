# Tests

Two layers of tests, both fast and dependency-free (no Postgres, PostGIS,
Docker, or network):

| Layer | Location | What it exercises |
|-------|----------|-------------------|
| **Unit** | `tests/unit/` | Pure logic and individual classes (e.g. `UserInfo` permission rules, a repository in isolation). |
| **Integration** | `tests/integration/` | Real HTTP requests through the real FastAPI app: routing, auth wiring, repositories, schemas, and serialization. |

## The mocking boundary: the "data fetcher", not the repository

Integration tests run the **real** routes and repositories. The only things
swapped out are:

1. **The database sessions** (`get_task_session`, `get_osm_session`) — replaced
   with a `FakeSession` that returns pre-programmed rows instead of running SQL.
   This is the "data fetcher" boundary: everything *above* the `AsyncSession`
   (repositories, models, routes, Pydantic serialization) runs for real.
2. **`validate_token`** — replaced with a real `UserInfo` built by the factories,
   skipping JWT decoding and the TDEI network call. The permission logic
   (`isWorkspaceLead`, `isWorkspaceContributor`, …) is still real.
3. **The upstream OSM client** (`api.main._osm_client`, proxy tests only) —
   replaced with a streamable mock transport.

Mocking at the session level (rather than mocking whole repositories) means
the SQLModel object construction, repository logic, route guards, and response
serialization are all genuinely tested.

## Writing an integration test

```python
async def test_get_workspace_by_id(client, login, task_session):
    login()                                              # authenticate
    task_session.queue(fakes.rows(factories.make_workspace(id=7)))
    response = await client.get("/api/v1/workspaces/7")
    assert response.status_code == 200
```

### Fixtures (`conftest.py`)

- `client` — async HTTP client bound to the app over an in-process ASGI transport.
- `login(user_info=None)` — set the authenticated user. Call with a
  `factories.make_user_info(...)` to control roles/permissions.
- `task_session` / `osm_session` — the two `FakeSession`s. Queue results on them.

### The call-order contract

Because the mock is at the session level, **queue results in the order the
repository issues queries**. Each result builder models one DB round-trip:

| Builder | Models | Returned by |
|---------|--------|-------------|
| `fakes.rows(*entities)` | a SELECT | `scalars().all()`, `scalar_one_or_none()`, `all()` |
| `fakes.empty()` | a SELECT with no rows | drives NotFound paths |
| `fakes.affected(n)` | an UPDATE/DELETE | `result.rowcount` |
| `fakes.mappings(*dicts)` | a raw-SQL result | `result.mappings()` |
| `fakes.scalar(value)` | `session.scalar(...)` | e.g. an `EXISTS` check |

Routes that touch both DBs (e.g. teams, workspace create/delete) queue results
on **both** `task_session` and `osm_session`. `SET search_path` statements are
recognized and do not consume a queued result. `commit`/`add`/`rollback` calls
are recorded on the session (`session.commits`, `session.added`, …) for assertions.

The repository docstrings and the existing tests show the query sequence for
each route.

## Running

```bash
uv run pytest                 # full suite with coverage (see pyproject.toml)
uv run pytest --no-cov -q     # quick, no coverage
uv run pytest tests/unit      # one layer
uv run pytest -k workspaces   # by keyword
```

## Layout

```
tests/
  conftest.py            # fixtures: client, login, task_session, osm_session
  support/
    fakes.py             # FakeSession + FakeResult + result builders
    factories.py         # make_user_info / make_workspace / make_user / make_team
    http.py              # streamable mock transport for the OSM proxy
  unit/
    test_user_info.py
    test_workspace_repository.py
  integration/
    test_health.py
    test_workspaces.py
    test_teams.py
    test_proxy.py
```
