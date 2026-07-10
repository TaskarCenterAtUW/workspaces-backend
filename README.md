# Workspaces Backend

## What this does

This is a combination API backend for workspaces, providing /workspaces* methods, as well as a proxy to 
the OSM API ("openstreetmap website") plus OSM CGI-map (the C-accelerated methods) that enforces 
authorization and authentication based on a TDEI/Keycloak JWT token (see main.py for this proxy logic). 

## Deployment architecture

The deployed system is defined by [`docker-compose.az.yml`](docker-compose.az.yml). It runs the
**application tier** as four containers; the **data tier** (Postgres/PostGIS) is external, managed
Azure Database for PostgreSQL, not part of this compose file.

When deployed by `workspaces-stack` this model also holds. 

```
                    client  (TDEI/Keycloak JWT)
                       │
                       ▼ :8000
        ┌──────────────────────────────────────┐
        │          workspaces-backend           │  this repo — FastAPI front door.
        │   authn/authz + OSM reverse proxy     │  Serves /api/v1/*, proxies the rest.
        └───┬──────────────────────────────┬────┘
     WS_OSM_HOST                   TASK_DATABASE_URL
   (→ osm-rails)                   OSM_DATABASE_URL
            │                              │
            ▼                              │
     ┌──────────────┐                      │
     │  osm-rails   │  OSM website (Rails); the single OSM entry point.
     │  :3000       │  Serves the API/UI and fronts cgimap for the
     └──────┬───────┘  performance-critical /api/0.6 calls.
            │ (internal)                   │
            ▼                              │
     ┌──────────────┐                      │
     │  osm-cgimap  │  C-accelerated /api/0.6 (map, changeset bulk)
     │  :8000       │                      │
     └──────────────┘                      │
                                           │
     osm-rails-worker  (rake jobs:work)    │  background jobs
            │                              │
            │  backend, rails, cgimap, worker all connect to ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  data tier — Azure Postgres (external, PostGIS)                   │
   │  opensidewalks-${ENV}.postgres.database.azure.com:5432            │
   │    • workspaces-tasks-${ENV}   TASK db  (alembic_task; backend)   │
   │    • workspaces-osm-${ENV}     OSM db   (alembic_osm; all four)   │
   └─────────────────────────────────────────────────────────────────┘
```

### Services

| Service | Image | Role |
|---|---|---|
| `workspaces-backend` | `workspaces-backend-v2:${ENV}` | This repo. The only host-exposed service (`8000:8000`). Validates the TDEI/Keycloak JWT, enforces workspace authorization, serves `/api/v1/*`, and proxies everything else to the OSM tier. Connects to **both** databases. |
| `osm-rails` | `workspaces-osm-rails-v2:${ENV}` | The OpenStreetMap website (Rails) — the **single OSM entry point** the backend proxies to (`WS_OSM_HOST`). Serves the OSM API/UI and fronts cgimap for the heavy `/api/0.6` calls. Connects to the OSM db. |
| `osm-cgimap` | `workspaces-osm-cgimap-v2:${ENV}` | C++ reimplementation of the performance-critical OSM `0.6` calls (map queries, changeset upload/download), sitting behind `osm-rails`. Tuned here for large imports (`CGIMAP_MAX_*`). Connects to the OSM db. |
| `osm-rails-worker` | `workspaces-osm-rails-v2:${ENV}` | Background job runner (`rake jobs:work`) for the Rails app. Connects to the OSM db. |

### Two databases

The backend holds two connections, and the two alembic trees target them independently (see
`CLAUDE.md` and `api/utils/migrations.py`):

* **TASK db** (`TASK_DATABASE_URL` → `workspaces-tasks-${ENV}`) — the workspaces + tasking-manager
  schema, built by the `alembic_task` tree. Only the backend connects here.
* **OSM db** (`OSM_DATABASE_URL` → `workspaces-osm-${ENV}`) — OSM data plus `users` and the
  `tasking_*` tables, built by the `alembic_osm` tree. The backend, cgimap, rails, and the worker
  all connect here.

On startup (outside of pytest) the backend runs `alembic -n task upgrade head` and
`alembic -n osm upgrade head`, applying each tree to its database.

### Environment templating

Every image tag, database name/user, and server host is parameterized by `${ENV}`
(`dev` / `stage` / `prod`), and secrets are injected from the shell environment
(`${WS_TASKS_DB_PASS}`, `${WS_OSM_DB_PASS}`, `${WS_OSM_SECRET_KEY_BASE}`). Branches map to these
environments — see the Branch Index below.

## Branch Index

* ```develop``` merge your work here; keep this up to date with the "development" environment / dev tag
* ```staging``` keep this up to date with the "staging" environment / stage tag
* ```production``` keep this up to date with the "production" environment / prod tag
  
## To start on your local machine for dev work

```
cp .env.example .env # edit this file for your config
uv sync
uv run uvicorn api.main:app
```

## Running the tests

Tests are fast and require no database, Docker, or network (see
`tests/README.md` for the design, and `CLAUDE.md` for conventions).

```
uv run pytest                 # full suite with coverage (configured in pyproject.toml)
uv run pytest --no-cov -q     # quick run, no coverage
uv run pytest tests/unit      # unit tests only
uv run pytest tests/integration  # integration tests only
uv run pytest -k workspaces   # filter by keyword
```

Type-check and format (matches the pre-commit hooks):

```
uvx pyright --pythonpath .venv/bin/python api tests
uv run black api tests && uv run isort api tests
```

