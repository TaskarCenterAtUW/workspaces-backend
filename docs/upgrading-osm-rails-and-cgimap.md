# Upgrading osm-rails and cgimap from upstream

`osm-rails` ([workspaces-openstreetmap-website](https://github.com/TaskarCenterAtUW/workspaces-openstreetmap-website))
and `osm-cgimap` ([workspaces-cgimap](https://github.com/TaskarCenterAtUW/workspaces-cgimap)) are forks
of [openstreetmap-website](https://github.com/openstreetmap/openstreetmap-website) and
[openstreetmap-cgimap](https://github.com/zerebubuth/openstreetmap-cgimap). This backend shares their
database. Bringing in upstream changes happens in this order:

1. Find what changed upstream.
2. Port the new Rails migrations into this repo's `alembic_osm` tree.
3. Fix anything in this backend that the upstream changes break.
4. Deploy this backend, which applies the schema changes at startup.
5. Upgrade osm-rails.
6. Upgrade cgimap.

The schema has to be in place before new Rails or cgimap code runs against it, and this backend is
what puts it there.

## Why the migrations are ported here

Rails defines the OSM schema, but its own `rails db:migrate` doesn't fit how Workspaces is deployed:

- **Workspace schemas.** Each workspace's data lives in its own schema, `"workspace-<id>"`, cloned
  from `public` by Apartment (see "Workspace schemas are clones of `public`" in `CLAUDE.md`). Rails'
  Apartment config migrates **every** workspace schema, which is about 1,900 on prod. Some
  migrations fail there: Rails drops each workspace's `users` copy, so `add_column :users` has no
  table to change.
- **No automatic run.** Nothing in the deploy runs `rails db:migrate`, but this backend runs
  `alembic upgrade head` at every startup (`api/utils/migrations.py`).

So each upstream migration is ported to an `alembic_osm` revision, which records the Rails version
numbers in `schema_migrations`. Rails then sees them as already applied and doesn't try to run them
itself.

## 1. Find what changed upstream

For each fork, compare its `develop` branch with upstream:

```bash
# workspaces-openstreetmap-website
git remote add upstream https://github.com/openstreetmap/openstreetmap-website.git
git fetch upstream master
mb=$(git merge-base upstream/master origin/develop)
git diff --name-only --diff-filter=A $mb upstream/master -- db/migrate   # new migrations
git diff $mb upstream/master -- db/structure.sql                          # the DDL they produce

# workspaces-cgimap
git remote add upstream https://github.com/zerebubuth/openstreetmap-cgimap.git
git fetch upstream master
mb=$(git merge-base upstream/master origin/develop)
git log --oneline origin/develop..upstream/master
git diff $mb upstream/master -- src/backend/apidb    # SQL changes: new tables/columns cgimap reads
```

If cgimap's queries need a column, find the Rails migration that adds it, and make sure that
migration is applied everywhere cgimap reads it (see step 6).

## 2. Port the migrations to `alembic_osm`

Write one revision per upstream sync. `alembic_osm/versions/6dafe2da5f69_mirror_rails_upstream_migrations.py`
is the worked example, and its integration test is `tests/integration/test_mirror_rails_upstream_migrations.py`.

- **Copy the DDL from upstream `db/structure.sql`, statement for statement.** Don't re-derive it from
  the Ruby: `structure.sql` is what Rails actually produces (sequence, default, primary key name,
  index and foreign key names).
- **Check before every step.** Rails may already have applied some of it, and the integration-test
  database is only a stub (PostGIS plus part of `users`). Use `to_regclass`, `ADD COLUMN IF NOT
  EXISTS` and similar, and skip a step whose prerequisite Rails table or type is missing.
- **Record the versions** in `public.schema_migrations`, but only for the steps that actually took
  effect.
- **Decide, for each migration, whether it also belongs in workspace schemas.** It does if anything
  running *inside* a workspace reads or writes it:
  - **Rails:** models that are not in Apartment's `excluded_models` (in the rails fork's
    `config/initializers/apartment.rb`) are looked up in the workspace schema only.
  - **cgimap and this backend:** they set `search_path` to `"workspace-<id>", public`, so they read
    the workspace's copy first.
  - **`users`:** it has no workspace copy.

  For example, the Noticed notification tables are per-workspace, because Rails writes a
  notification inside the workspace when someone comments on a changeset or note. The other eight
  changes in `6dafe2da5f69` are `public`-only.
- **Changes to workspace schemas follow the rules in `CLAUDE.md`:**
  - One transaction per schema, never one transaction for all of them. That caused the 2026-10-02
    prod outage.
  - Visit only the schemas that need work, and do each in one round trip (`6dafe2da5f69` uses a
    single `DO` block per schema).
  - Report a failing schema and carry on. Print progress and the total time.
  - The whole pass has to fit in the startup time limit (230 s) at prod's ~1,900 schemas. Test with
    a few hundred.
  - Also record the versions in each workspace's `schema_migrations` copy, so Rails' per-workspace
    migrate skips them.
- **Run the `public` changes in their own short transaction, committed before the workspace pass.**
  That keeps locks on busy tables like `users` brief. It also means any workspace created during
  the pass is cloned with the new tables already present.
- **Wrap that work in `op.get_context().autocommit_block()`.** When an upgrade runs several
  revisions, Alembic puts them all in one transaction, and it keeps holding the locks the earlier
  revisions took (for example on `users`). A separate connection that then alters `users` waits
  for that transaction, which is itself waiting for the migration. Postgres can't detect that
  deadlock, so startup hangs. The first draft of `6dafe2da5f69` hung like this on a fresh database.
- **Don't add Rails tables to `SERVICE_TABLES`.** That list is for this backend's own tables, whose
  workspace copies get dropped.
- **Test against upstream's text.** The integration test should compare columns, defaults, indexes
  and constraints with what `structure.sql` says, and cover the per-schema pass at scale plus a
  schema that fails.

## 3. Check this backend for breaking changes

- **`users`:** this backend inserts `users` rows itself (`api/core/security.py`,
  `api/src/tasking/projects/repository.py`).
  - A new NOT NULL column needs a default.
  - Check upstream changes to the `User` model's validations (`app/models/user.rb`). A
    backend-provisioned row must satisfy them, or Rails operations on that user fail (see
    "Provisioning gotcha" in `CLAUDE.md`).
- **The token bridge:** it relies on Doorkeeper storing tokens in plaintext. Check upstream changes
  to `config/initializers/doorkeeper.rb`.
- **Table names:** check new Rails tables against this backend's own (`teams`, `team_user`,
  `tasking_*`, `jobs`, ...).
- **Proxied routes:** look for changed status codes or response fields on the routes this backend
  proxies (for example, cgimap 2.1.1 returns 429 instead of 509 for its bandwidth limit).
- **Alembic autogenerate:** never run it unfiltered against the OSM database. It would propose
  dropping every Rails table and column this tree doesn't model.

## 4. Deploy this backend

Deploy to dev, then stage, then prod, through the usual `develop` → `staging` → `production` PRs.

- **Startup log:** check it for each environment, e.g. `public: Rails migrations in effect: ...`
  and `Workspace notification tables: added to N schema(s), 0 left ... in Ns`.
- **Timing:** stage has fewer workspaces than prod, so check how long the workspace pass took there
  and scale it up before restarting prod.
- **Rollback:** the previous image doesn't know the new revision, so its `alembic upgrade head`
  fails at startup. Before starting an older image, run
  `alembic -n osm stamp <previous revision>`. That doesn't undo the schema changes; the Rails
  objects can stay.

## 5. Upgrade osm-rails

1. Merge upstream into a branch off the fork's `develop` and open a PR into `develop`. The fork's
   `push-docker-image.yml` builds `:dev` on merge.
2. Before migrating anything, run `bin/rails db:migrate:status` in the dev rails container. Every
   ported version should show `up`. A `down` version you didn't port means step 2 is incomplete.
   - The fork's own migrations can collide with tables this backend created. For example, the
     fork's `CreateTeam` and this backend's `teams`/`team_user` are defined differently. Guard
     these with `table_exists?`.
3. Add new global models to Apartment's `excluded_models`.
4. Promote `develop` → `staging` → `production`. Each `gateway-<env>` Container App picks up its
   new `:<env>` image only when its revision restarts.

## 6. Upgrade cgimap

1. Merge upstream into a branch off the fork's `develop` and open a PR into `develop`. The fork's
   `push-docker-image.yml` builds `docker/ubuntu/Dockerfile2404`, which matches what prod runs.
2. Before deploying to an environment, check that every schema cgimap reads has the columns its new
   queries need. This read-only query lists the schemas missing one; adjust the table and column
   names:

   ```sql
   SELECT t.table_schema FROM information_schema.tables t
   WHERE t.table_name = 'changesets'
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns c
                     WHERE c.table_schema = t.table_schema AND c.table_name = 'changesets'
                       AND c.column_name = 'num_created_nodes');
   ```

3. Promote `develop` → `staging` → `production`, and restart each `gateway-<env>` revision.

## Checks after each environment

Using a workspace created before the upgrade:
- `map` requests, and a changeset upload and download (cgimap).
- A changeset read (`/api/0.6/changeset/<id>` and `.json`).
- A changeset comment and a note comment (Rails, which writes notifications in the workspace).
- No new errors in the gateway's cgimap or rails logs, or in this backend's startup log.
