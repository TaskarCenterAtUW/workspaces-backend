# Workspaces Backend

## What this does

This is a combination API backend for workspaces, providing /workspaces* methods, as well as a proxy to 
the OSM API ("openstreetmap website") plus OSM CGI-map (the C-accelerated methods) that enforces 
authorization and authentication based on a TDEI/Keycloak JWT token (see main.py for this proxy logic). 

## Repo Branch Index

* ```develop``` merge your work here; keep this up to date with the "development" environment / dev tag
* ```staging``` keep this up to date with the "staging" environment / stage tag
* ```production``` keep this up to date with the "production" environment / prod tag

## Development with local environment

Use the file `docker-compose.local.yml` to build and deploy local code changes. This allows you to run the entire system at once instead of 
connecting to existing Databases.

### To start on your local machine for dev work (no docker)

```
cp .env.example .env # edit this file for your config
uv sync
uv run uvicorn api.main:app
```

### Initial setup for development local environment 

Step 1: Login to azure docker

The docker compose relies on images in `opensidewalksdev` azure container registry. Make sure your docker system is logged into it before pulling the images and trying to run the containers.

Docker login command: 

`docker login opensidewalksdev.azurecr.io -u opensidewalksdev `

Password needs to be obtained from Azure portal

Step 2: Run docker compose for the first time

Use the following command to start the containers first time

`docker compose --file docker-compose.local.yml  up --build`

Step 3: Run the migration scripts.

You will observe that only `osm-rails` component seems to work but the backend and other services may be down. this is because the database migrations on the base osm database are not done. To do the base migrations, do the following:

- Connect to the `osm-rails` container. If you are using docker hub for desktop, just go to the exec section of the container.
  If you want to use command line, execute the command `docker exec -it <container_name_or_id> /bin/bash` where `container_name` is the name of osm-rails container
- Execute the migration script in the /bin/bash with `bundle exec rails db:migrate`
- The above command runs the migration script for databases

Step 4: Add `workspaces-tasks-local` database in postgresql

Workspaces backend relies on an additional database. This is needed for some older migrations code.

- Connect to `database` container.
- Run the following set of commands one by one

```shell
psql --username postgres
create database "workspaces-tasks-local";
exit;
psql --username postgres --dbname "workspaces-tasks-local";
create extension if not exists postgis;

```

Step 5: Restart the docker compose again

- `docker compose --file docker-compose.local.yml down`
- `docker compose --file docker-compose.local.yml up --build`

### Commands to start and stop the docker compose

`docker compose --file docker-compose.local.yml  up --build -d`

`docker compose --file docker-compose.local.yml down`

Backend code will be available at `http://localhost:8000`


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
## Deployment architecture

The deployed system runs the **application tier** as five containers; the **data tier**
(Postgres/PostGIS) is external, managed Azure Database for PostgreSQL.

> **Note on compose files.** The production environment is currently provisioned **manually on
> Azure**. Neither the `workspaces-stack` compose files nor
> [`docs/old/docker-compose.az.yml`](docs/old/docker-compose.az.yml) (retired to `docs/old/` for
> exactly this reason) describe what is running — they are historical intent only. Where this
> document says "deployed", it means verified against the live host — see
> [What is actually deployed at the OSM edge](#what-is-actually-deployed-at-the-osm-edge).

```
                    client  (TDEI/Keycloak JWT)
                       │
                       ▼ :8000
        ┌──────────────────────────────────────┐
        │          workspaces-backend          │  this repo — FastAPI front door.
        │   authn/authz + OSM reverse proxy    │  Serves /api/v1/*, proxies the rest.
        └───┬──────────────────────────────┬───┘  Also serves the public OSM host.
     WS_OSM_HOST                   TASK_DATABASE_URL
   (→ osm-web)                     OSM_DATABASE_URL
            │                              │
            ▼                              │
     ┌──────────────┐                      │
     │   osm-web    │  lighttpd. THE dispatcher: rewrites the heavy
     │   :80        │  /api/0.6 paths to cgimap over FastCGI and
     └──┬────────┬──┘  proxies everything else to rails. See docs/deploy/
        │        │                         │
        ▼        ▼                         │
 ┌────────────┐ ┌──────────────┐           │
 │ osm-rails  │ │  osm-cgimap  │  C-accelerated /api/0.6
 │ :3000      │ │  :8000       │  (map, changeset bulk)
 └────────────┘ └──────────────┘           │
   OSM website (Rails);                    │
   API + web UI                            │
                                           │
     osm-rails-worker  (rake jobs:work)    │  background jobs
            │                              │
            │  backend, rails, cgimap, worker all connect to ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  data tier — Azure Postgres (external, PostGIS)                  │
   │  opensidewalks-${ENV}.postgres.database.azure.com:5432           │
   │    • workspaces-tasks-${ENV}   TASK db  (alembic_task; backend)  │
   │    • workspaces-osm-${ENV}     OSM db   (alembic_osm; all four)  │
   └─────────────────────────────────────────────────────────────────┘
```

### Services

| Service | Image | Role |
|---|---|---|
| `workspaces-backend` | `workspaces-backend-v2:${ENV}` | This repo. The only host-exposed service (`8000:8000`). Validates the TDEI/Keycloak JWT, enforces workspace authorization, serves `/api/v1/*`, and proxies everything else to the OSM tier. Connects to **both** databases. |
| `osm-web` | `workspaces-osm-web:${ENV}` | **lighttpd.** The single OSM entry point the backend proxies to (`WS_OSM_HOST`, default `http://osm-web`). Owns the rails-vs-cgimap split: a fixed set of heavy `/api/0.6` paths is rewritten to `dispatch.map` and passed to cgimap over FastCGI; everything else is proxied to rails. Config vendored at [`docs/deploy/lighttpd.conf`](docs/deploy/lighttpd.conf). Holds no DB connection. |
| `osm-rails` | `workspaces-osm-rails-v2:${ENV}` | The OpenStreetMap website (Rails). Serves the OSM API and web UI. Reached only *via* `osm-web` — it does **not** front cgimap. Connects to the OSM db. |
| `osm-cgimap` | `workspaces-osm-cgimap-v2:${ENV}` | C++ reimplementation of the performance-critical OSM `0.6` calls (map queries, changeset upload/download). Sits behind **`osm-web`** (FastCGI on :8000), not behind rails. Tuned here for large imports (`CGIMAP_MAX_*`). Connects to the OSM db. |
| `osm-rails-worker` | `workspaces-osm-rails-v2:${ENV}` | Background job runner (`rake jobs:work`) for the Rails app. Connects to the OSM db. |

### What is actually deployed at the OSM edge

The `osm-web` container runs **lighttpd**, not nginx. Verified against the live public OSM host,
which returns the upstream's `Server` header alongside the backend's own:

```
$ curl -sSI https://osm.workspaces.sidewalks.washington.edu/api/capabilities.json
Server: uvicorn            <- this backend
Server: lighttpd/1.4.64    <- osm-web
```

That config is vendored here as [`docs/deploy/lighttpd.conf`](docs/deploy/lighttpd.conf), copied
from `workspaces-stack`. **Two traps for the next reader:**

1. The upstream file's own header says *"We don't use lighttpd anymore. Nginx replaced lighttpd."*
   That is not true of the deployed environment — lighttpd is what answers in production. A sibling
   `nginx.conf` exists in `workspaces-stack` and is the intended successor, but it is not deployed.
2. The two configs are **not** equivalent. Only `nginx.conf` implements the `/workspace/{id}/` path
   prefix, the `x_workspace` query parameter, and Basic-auth TDEI tokens — so none of those work
   today, at any layer.

Also note the public OSM host resolves to **this backend**, not to `osm-web`: `/health` and `/docs`
answer there. Every OSM request therefore passes through `validate_token`, the `X-Workspace` gate,
and the CORS middleware in `api/main.py` before reaching lighttpd. One consequence: the Rails web UI
is unreachable through the proxy (`GET /login` returns `401 {"detail":"Not authenticated"}`), because
`catch_all` requires a Bearer token and an `X-Workspace` header for everything outside
`TENANT_BYPASSES`.

Deploying `nginx.conf` as-is would **not** by itself enable the three features in trap 2: the backend
rejects those requests (400 for a missing `X-Workspace`, 401 for a non-Bearer scheme) before they
ever reach `osm-web`. Enabling them requires changes in `api/main.py` too.

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

### What the proxy must provide for osm-rails / osm-web

This backend is the **only entry point** to the OSM tier (osm-rails + cgimap,
behind `osm-web`, which runs lighttpd): in the deployment, the public OSM host routes to this
container, which proxies `/api/0.6/*` to `WS_OSM_HOST` (default
`http://osm-web`). For the OSM services to work, the proxy must uphold the
following contract. `CLAUDE.md` has the full rationale.

1. **Bridge TDEI auth into OSM's OAuth2.** osm-rails authenticates the API *only*
   via doorkeeper OAuth2 (`oauth_access_tokens`); it has no TDEI/JWT path. So on
   token validation the backend mirrors the TDEI JWT into `oauth_access_tokens`
   in the OSM DB (the "token bridge" in `api/core/security.py`), and forwards the
   incoming `Authorization: Bearer <token>` header unchanged. Then osm-rails and
   cgimap authenticate the token via plain OAuth2. Controlled by
   `WS_OSM_TOKEN_BRIDGE_ENABLED` (on by default) — with it off, osm-rails returns
   **401** for TDEI tokens. The backend auto-creates the doorkeeper application
   (and a system user to own it), so no manual OSM setup is required.

2. **Provision valid OSM `users` rows.** The backend creates OSM `users` rows for
   TDEI users (`auth_provider='TDEI'`, `auth_uid` = the JWT `sub`). These must
   satisfy OSM's `User` validations — in particular `pass_crypt` length 8..255.
   A too-short value is invisible to cgimap but makes osm-rails operations that
   re-validate the user fail (e.g. posting a changeset comment or a note),
   surfacing as *"Unable to serialize … without an id"*. The
   `alembic_osm` migration `*_heal_short_tdei_pass_crypt` repairs legacy rows.

3. **Carry workspace tenancy.** Workspace-scoped OSM requests must include an
   `X-Workspace: <id>` header. The proxy authorizes it against the caller's
   workspaces and forwards it (it is *not* stripped) so cgimap/osm-rails scope to
   the `workspace-<id>` schema. A few paths are exempt (`TENANT_BYPASSES` in
   `api/main.py`): workspace create/delete (`PUT`/`DELETE /api/0.6/workspaces/{id}`)
   and user provisioning during sign-in (`PUT /api/0.6/user/{uid}`).

4. **Set the proxy headers.** The proxy rewrites `Host` to the OSM host and sets
   `X-Real-IP` / `X-Forwarded-For` / `-Host` / `-Proto`, while stripping
   hop-by-hop headers and any spoofed forwarding headers from the client. It does
   *not* strip `Authorization` or `X-Workspace`.

5. **Connectivity.** `WS_OSM_HOST` must reach `osm-web`, and the backend needs
   **both** `OSM_DATABASE_URL` and `TASK_DATABASE_URL` — the token bridge and
   user provisioning write to the OSM database.
