# Workspaces Backend

## What this does

This is a combination API backend for workspaces, providing /workspaces* methods, as well as a proxy to 
the OSM API ("openstreetmap website") plus OSM CGI-map (the C-accelerated methods) that enforces 
authorization and authentication based on a TDEI/Keycloak JWT token (see main.py for this proxy logic). 

## What the proxy must provide for osm-rails / osm-web

This backend is the **only entry point** to the OSM tier (osm-rails + cgimap,
behind `osm-web`): in the deployment, the public OSM host routes to this
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

## Development with local environment

Use the file `docker-compose.local.yml` to build and deploy local code changes. This allows you to run the entire system at once instead of 
connecting to existing Databases.

### Initial setup.
- On first launch, rails-worker will fail because migrations are not done
- Go to the `osm-rails` `/bin/sh` and execute `bundle exec rails db:migrate`
- The above script runs the migration code for osm-rails
- The rails-worker will be able to run
- The backend code will fail first time becase `workspaces-tasks-local` database is not available
- Login to postgresql container and run the following commands
   `psql --username postgres`
   `create database "workspaces-tasks-local";`
   `psql --username postgres --dbname "workspaces-tasks-local";`
   `create extension if not exists postgis;`
- Run the backend code now and it should be able to run

### Commands to start and stop the docker compose

`docker compose --file docker-compose.local.yml  up --build -d`

`docker compose --file docker-compose.local.yml down`

Backend code will be available at `http://localhost:3000`