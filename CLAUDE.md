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

## What Each Role Can Do

Project Lead
* Edit Metadata
* Edit Longform Quests
* Toggle App-Enabled Flag
* Delete Workspace
* Define User Teams
* Define Groups or Roles
* Export to TDEI
* Validate Changeset
* Move Workspace from Project Group to Project Group
* Edit POSM Element

Validator
* Export to TDEI 
* Validate Changeset
* Edit POSM Element

Contributor
* Edit POSM Element

Authenticated User With PG/Workspace Association
* Edit POSM Element

### What this backend actually enforces (vs. the matrix above)

The matrix above is the intended product model. It is only **partially**
enforced in `api/` — this service is a proxy in front of the OSM website,
cgimap, and TDEI, so several capabilities are enforced downstream (or not yet
at all). Validated against the code:

**Enforced here, Lead-gated (`isWorkspaceLead` → 403).** POC inherits these
(POC on the owning project group satisfies `isWorkspaceLead`):

| Capability | Endpoint |
|---|---|
| Edit Metadata | PATCH `/workspaces/{id}` |
| Edit Longform Quests | PATCH `/workspaces/{id}/quests/long/settings` |
| Toggle App-Enabled Flag | PATCH `/workspaces/{id}` (`externalAppAccess`) |
| Delete Workspace | DELETE `/workspaces/{id}` |
| Define User Teams | `/workspaces/{id}/teams...` (create/update/delete/members) |
| Define Groups or Roles | PUT/DELETE `/workspaces/{id}/users/{user_id}...` |

**Not enforced / not present here:**

* **Export to TDEI** — no endpoint exists in this backend.
* **Move Workspace PG→PG** — not possible; `WorkspacePatch` has no
  `tdeiProjectGroupId` field, so no route can change a workspace's project group.
* **Edit POSM Element** — goes through the OSM proxy catch-all
  (`api/main.py`), which gates *every* proxied operation on
  `isWorkspaceContributor` alone. There is no Validator- or Lead-level check on
  proxied traffic. Raw changeset commits (proxied `PUT /api/0.6/changeset/...`)
  are likewise Contributor-gated; the proxy only *tags* a contributor's new
  changeset with `review_requested=yes` when the workspace has `autoFlagReview`
  set — it does not enforce validation.

**Enforced here, Validator-gated (`isWorkspaceLead || isWorkspaceValidator` →
403).** This is a native FastAPI route, not proxied traffic. Leads (and POC via
`isWorkspaceLead`) inherit it:

| Capability | Endpoint |
|---|---|
| Resolve/Validate Changeset | PUT `/workspaces/{id}/changesets/{changeset_id}/resolve` |

Resolving clears the `review_requested` tag and stamps `reviewed_by` with the
reviewer's UUID. The gate is enforced both in the route
(`api/src/osm/routes.py`) and, defensively, inside
`OSMRepository.resolveChangeset` (`api/src/osm/repository.py`).

**The Validator role grants exactly one thing at this layer:** the ability to
resolve changesets via the endpoint above. Aside from that, a Validator and a
Contributor have identical permissions in this backend. `isWorkspaceValidator`
otherwise only appears in the `role` field of `WorkspaceResponse`.

**"Contributor" and "Authenticated User With PG/Workspace Association" are the
same gate.** `isWorkspaceContributor` simply checks whether the workspace is in
one of the user's project groups (`accessibleWorkspaceIds`), i.e. PG/workspace
association — so both rows collapse to the same check.

Changeset *resolution* is Validator/Lead-gated here (see above). But the
Validator/Lead distinction on raw changeset *commits* and TDEI export is not
enforced at this layer; if required, it must be enforced downstream
(`workspaces-openstreetmap-website/`, `workspaces-cgimap/`) — that has not been
audited here.

## The OSM proxy layer: routing, auth, and user provisioning

This service is a reverse proxy in front of the OSM website (`osm-rails`) and
cgimap. The non-obvious parts, learned the hard way:

### Deployment routing (workspaces-stack)

In `workspaces-stack`, the **api container (this backend) serves the public OSM
host** (`osm.workspaces-<env>...`) alongside the API hosts — its traefik router
rule is `Host(new-api) || Host(api) || Host(osm)`, and the `osm-web` /
`osm-log-proxy` routers are commented out. So **every OSM call the frontend
makes routes through this backend**: `validate_token`, the `X-Workspace` gate,
and the CORS middleware all apply. The backend then proxies `/api/0.6/*` to
`WS_OSM_HOST` (config default `http://osm-web` — the internal service, *not* the
public domain). `osm-web`'s nginx splits traffic: changeset read/write subpaths
to cgimap, everything else to osm-rails.

The frontend (`services/osm.ts`) calls the OSM host directly with
`credentials: 'include'` + a Bearer TDEI token. Because it's *credentialed*
CORS, `CORS_ORIGINS` must list the exact frontend origin (no `*`;
`allow_credentials` is on). The stack supplies `WS_API_CORS_ORIGINS` as a **JSON
array** (`["https://..."]`), while older config comma-split it — a JSON array
would then become one malformed origin. `api/main.py` therefore reads
`settings.cors_origins_list`, which parses **both** a JSON array and a
comma-separated string (and `*`); set `CORS_ORIGINS` in either form.

### Two databases; `users` is owned by OSM Rails

`TASK_DATABASE_URL` (`alembic_task` tree) holds workspaces / tasking-manager
tables; `OSM_DATABASE_URL` (`alembic_osm` tree) holds OSM data, `users`, and the
`tasking_*` tables. The **`users` table is owned by the OSM Rails website**, not
either alembic tree — the trees only FK to it, and integration tests stub it
(`tests/integration/conftest.py`). The backend provisions `users` rows itself
via raw SQL with `auth_provider='TDEI'` and `auth_uid = str(<JWT sub>)` (the OSM
`auth_uid` **is** the token's `sub` claim).

### How OSM authenticates, and the TDEI token bridge

* **osm-rails** authenticates API calls *only* via **doorkeeper OAuth2**: it
  looks the bearer token up in `oauth_access_tokens` (`token` →
  `resource_owner_id` → `users.id`). Doorkeeper stores tokens **plaintext**
  (`SecretStoring::Plain`), so the raw JWT matches directly. It has **no**
  TDEI/JWT auth path.
* **cgimap** has both: the oauth2 lookup *and* a custom TDEI path
  (`get_user_id_for_tdei_token`, which verifies the JWT and matches
  `users.auth_uid`).

To make TDEI tokens work against osm-rails without forking it, the **token
bridge** (`_bridge_token_to_osm` in `api/core/security.py`) mirrors a validated
TDEI JWT into `oauth_access_tokens` — on the cache-miss path of `validate_token`
(so ~once per token, not per request). Gated by `WS_OSM_TOKEN_BRIDGE_ENABLED`.
It auto-provisions everything: a dedicated **system user** (`WS_OSM_SYSTEM_USER_*`)
to own the doorkeeper `oauth_applications` row it creates (keyed by
`WS_OSM_OAUTH_CLIENT_UID`; `oauth_applications.owner_id` is a NOT-NULL FK to
`users`, hence the system user), the caller's `users` row, and the plaintext
`oauth_access_tokens` row (`expires_in` from the JWT `exp`;
`ON CONFLICT (token) DO UPDATE` so re-presenting reactivates it). On token
rotation (new `jti`) the superseded token is revoked. Since the token then lives
in `oauth_access_tokens`, both osm-rails and cgimap authenticate it via plain
OAuth2 — cgimap's custom TDEI path becomes redundant.

### Provisioning gotcha: `pass_crypt` must be 8..255 chars

The OSM `User` model has `validates :pass_crypt, :length => 8..255`. When the
backend provisions a `users` row, **`pass_crypt` must be 8–255 chars** (use a
random throwaway — TDEI manages auth, so it's never used to log in). A too-short
value (the old `'none'`) makes the user *invalid*. This is silent for **cgimap**
operations — cgimap runs no Rails validations — but breaks any **osm-rails**
operation that re-validates the author via `validates :author, :associated =>
true`: notably `POST /api/0.6/changeset/{id}/comment` and note comments. The
comment fails to save (no `id`), then rendering it throws *"Unable to serialize
… without an id"*. Both provisioning paths (`_ensure_osm_user`,
`_provision_users_from_tdei`) set a valid `pass_crypt`; migration
`alembic_osm/versions/f3a7b9c1d2e4` heals legacy short rows. General principle:
a backend-provisioned `users` row must satisfy OSM's `User` validations (also
`display_name` 3..255 + unique, `email` present + unique) or Rails operations
that touch it will fail even though cgimap operations succeed.

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
are framework false positives. Suppress them with **targeted, inline**
`# pyright: ignore[<rule>]` comments at the specific offending call sites (e.g.
`# pyright: ignore[reportArgumentType]` on a `.where(Column == value)` line) —
not blanket file-level `# pyright:` directives, which would hide genuine errors
of those rules elsewhere in the file. Note Black may wrap a long query line and
move a trailing comment off the flagged line; place the ignore on the line
Pyright actually reports (often the inner `== value` line) so it survives
formatting. Keep `api/` and `tests/` at zero Pyright errors.

### Alembic enum migrations

Postgres `ENUM` types must be created/dropped idempotently. Declare the enum
with `create_type=False` and manage it explicitly with
`enum.create(op.get_bind(), checkfirst=True)` / `enum.drop(..., checkfirst=True)`
so a migration is safe whether or not the type already exists (and never
double-creates it via implicit table DDL).

