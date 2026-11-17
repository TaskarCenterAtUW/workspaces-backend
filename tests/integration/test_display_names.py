"""Display names for TDEI users' OSM ``users`` rows, against a real database.

The OSM display name is public. It comes from the person's name, never their
email, and has to be unique the way OSM compares it: case-insensitively, after
NFKC normalisation. A row whose display name is the user's own email (written by
older code from ``preferred_username``) gets a real name.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import api.core.security as security

pytestmark = pytest.mark.integration


async def _insert_user(conn, *, auth_uid, email, display_name):
    await conn.execute(
        text(
            "INSERT INTO users (auth_uid, email, display_name, auth_provider) "
            "VALUES (:auth_uid, :email, :display_name, 'TDEI')"
        ),
        {"auth_uid": auth_uid, "email": email, "display_name": display_name},
    )


async def _display_name(conn, auth_uid):
    return (
        await conn.execute(
            text("SELECT display_name FROM users WHERE auth_uid = :auth_uid"),
            {"auth_uid": auth_uid},
        )
    ).scalar_one()


async def test_a_taken_name_is_matched_case_insensitively_and_normalised(
    _migrated_db,
):
    _task_url, osm_url = _migrated_db
    tag = uuid4().hex[:6]
    taken, newcomer = str(uuid4()), str(uuid4())
    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            await _insert_user(
                conn,
                auth_uid=taken,
                email=f"{tag}@example.com",
                display_name=f"Alice Example {tag}",
            )

        async with AsyncSession(engine) as session:
            # Another user with the same name, in other case or full-width
            # letters (NFKC folds them), gets part of their id added.
            for name in [f"alice example {tag}", f"Ａｌｉｃｅ Example {tag}"]:
                chosen = await security.choose_osm_display_name(
                    session, name=name, auth_uid=newcomer
                )
                assert chosen == f"{name} {newcomer.replace('-', '')[:8]}"

            # The user's own row doesn't count against them.
            chosen = await security.choose_osm_display_name(
                session, name=f"Alice Example {tag}", auth_uid=taken
            )
            assert chosen == f"Alice Example {tag}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE auth_uid = ANY(:uids)"),
                {"uids": [taken, newcomer]},
            )
        await engine.dispose()


async def test_only_an_email_display_name_is_replaced(_migrated_db):
    _task_url, osm_url = _migrated_db
    tag = uuid4().hex[:6]
    leaked, named = str(uuid4()), str(uuid4())
    engine = create_async_engine(osm_url)
    try:
        async with engine.begin() as conn:
            await _insert_user(
                conn,
                auth_uid=leaked,
                email=f"carol.{tag}@example.com",
                display_name=f"Carol.{tag}@Example.com",
            )
            await _insert_user(
                conn,
                auth_uid=named,
                email=f"dee.{tag}@example.com",
                display_name=f"Dee {tag}",
            )

        async with AsyncSession(engine) as session:
            await security._replace_email_display_name(
                session, auth_uid=leaked, name=f"Carol Example {tag}"
            )
            await security._replace_email_display_name(
                session, auth_uid=named, name=f"Someone Else {tag}"
            )

        async with engine.connect() as conn:
            assert await _display_name(conn, leaked) == f"Carol Example {tag}"
            assert await _display_name(conn, named) == f"Dee {tag}"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE auth_uid = ANY(:uids)"),
                {"uids": [leaked, named]},
            )
        await engine.dispose()
