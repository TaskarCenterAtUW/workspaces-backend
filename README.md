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