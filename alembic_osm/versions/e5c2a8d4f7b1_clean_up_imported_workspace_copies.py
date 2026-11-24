"""clean up table copies in imported workspaces, and misplaced OSM logins

Two leftovers in workspace schemas, both found 2026-11-24.

1. This service's table copies, again. `b7d4e2a9c1f0` dropped them from every
   workspace schema, and since then the backend drops them from each new
   workspace as Rails creates it (`OSMRepository.dropServiceTableCopies`, on the
   proxied `PUT /api/0.6/workspaces/{id}`). Workspaces the importer created
   never went through that path: `ws-importer-*` called the OSM gateway
   directly (dev until 2026-11-23, stage until 2026-11-24, prod still), so each
   imported workspace kept its copies -- 206 schemas on dev, 162 on stage, 193
   on prod, all empty. This runs `b7d4e2a9c1f0`'s merge-and-drop again,
   unchanged: any rows a copy holds are merged into `public` first, and a copy
   it cannot merge is kept and reported.

2. OSM logins the token bridge wrote into workspace schemas. Rails' tables are
   cloned too, and while the workspace search_path leaked onto pooled
   connections (fixed 2026-10 with SET LOCAL) some of the bridge's unqualified
   inserts landed in a workspace's copy of `oauth_applications` and
   `oauth_access_tokens`: on prod 4 application rows and 444 tokens in 4
   schemas, on dev 3 and 32 in 3, all written 2026-07-13 to 09-25. Doorkeeper's
   models are in Apartment's excluded_models, so Rails reads those tables only
   from `public`; the rows were never used, every token has expired, and each
   application row duplicates public's. This deletes the rows and keeps the
   tables, because Rails' per-workspace migrations can resolve to them.

Both work one schema per transaction, like `b7d4e2a9c1f0` and `6dafe2da5f69`:
a schema that fails is reported and left as it was, and the app still starts.

Revision ID: e5c2a8d4f7b1
Revises: 6dafe2da5f69
Create Date: 2026-11-24 00:00:00.000000

"""

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision: str = "e5c2a8d4f7b1"
down_revision: Union[str, None] = "6dafe2da5f69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Deleted in this order: grants and tokens reference applications.
OAUTH_TABLES = ["oauth_access_grants", "oauth_access_tokens", "oauth_applications"]

# Schemas checked per transaction when looking for rows: each check takes a
# lock on every table it reads until the transaction ends.
_FIND_BATCH = 200

_WORKSPACE_SCHEMA = re.compile(r"workspace-[0-9]+")


def _copies_cleanup() -> ModuleType:
    """`b7d4e2a9c1f0`, loaded from its file (revision files are not a package)."""
    path = Path(__file__).with_name("b7d4e2a9c1f0_drop_workspace_table_copies.py")
    spec = importlib.util.spec_from_file_location("_b7d4e2a9c1f0", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schemas_with_oauth_rows(conn: Connection) -> list[str]:
    copies = conn.execute(
        text(
            "SELECT n.nspname, c.relname FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname ~ '^workspace-[0-9]+$' AND c.relkind = 'r'"
            " AND c.relname = ANY(:tables) ORDER BY n.nspname"
        ),
        {"tables": OAUTH_TABLES},
    ).all()

    found: set[str] = set()
    with conn.engine.connect() as find_conn:
        for i in range(0, len(copies), _FIND_BATCH):
            checks = []
            for schema, table in copies[i : i + _FIND_BATCH]:
                assert _WORKSPACE_SCHEMA.fullmatch(schema), schema
                checks.append(
                    f"SELECT '{schema}' WHERE EXISTS"
                    f" (SELECT 1 FROM {_q(schema)}.{_q(table)})"
                )
            with find_conn.begin():
                found.update(
                    find_conn.execute(text(" UNION ALL ".join(checks))).scalars()
                )
    return sorted(found)


def delete_misplaced_oauth_rows(conn: Connection) -> None:
    """Delete the rows in workspace schemas' copies of the OAuth tables, one
    schema per transaction. `public` is never touched."""
    schemas = _schemas_with_oauth_rows(conn)
    done = failed = 0
    for schema in schemas:
        try:
            with conn.engine.begin() as schema_conn:
                present = set(
                    schema_conn.execute(
                        text(
                            "SELECT relname FROM pg_class"
                            " WHERE relnamespace = to_regnamespace(:s)"
                            " AND relkind = 'r' AND relname = ANY(:tables)"
                        ),
                        {"s": _q(schema), "tables": OAUTH_TABLES},
                    ).scalars()
                )
                deleted = []
                for table in OAUTH_TABLES:
                    if table in present:
                        n = schema_conn.execute(
                            text(f"DELETE FROM {_q(schema)}.{_q(table)}")
                        ).rowcount
                        if n:
                            deleted.append(f"{n} {table}")
            print(f"{schema}: deleted misplaced OSM logins: {', '.join(deleted)}")
            done += 1
        except Exception as e:  # noqa: BLE001 -- reported, and the rest continue
            failed += 1
            print(
                f"{schema}: OSM logins not deleted, left as it was: {type(e).__name__}: {e}"
            )
    print(
        f"Misplaced OSM logins: {done} workspace schema(s) cleaned, {failed} left"
        " as they were after an error"
    )


def upgrade() -> None:
    bind = op.get_bind()
    # Commit Alembic's transaction first, as 6dafe2da5f69 does: when several
    # revisions run in one upgrade it still holds their locks, and the
    # per-schema connections below would wait on it.
    with op.get_context().autocommit_block():
        _copies_cleanup().merge_and_drop_copies(bind)
        delete_misplaced_oauth_rows(bind)


def downgrade() -> None:
    # The copies were never meant to exist, and the deleted logins were never
    # readable by Rails.
    pass
