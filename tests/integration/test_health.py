"""Integration smoke tests for unauthenticated endpoints."""

import asyncio

from api import main
from tests.support import fakes


async def test_health_check(client, monkeypatch, task_session, osm_session):
    monkeypatch.setenv("CODE_VERSION", "abc123")
    task_session.queue(fakes.rows("c93bbbfd4279"))
    osm_session.queue(fakes.rows("b7d4e2a9c1f0"))
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "abc123",
        "schema": {"task": "c93bbbfd4279", "osm": "b7d4e2a9c1f0"},
    }


async def test_health_check_without_code_version(client, monkeypatch):
    monkeypatch.delenv("CODE_VERSION", raising=False)
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["version"] == "unknown"


async def test_health_check_reports_unreadable_database_as_null(
    client, task_session, osm_session
):
    """A database that can't be read doesn't fail the check, or hide the other."""
    task_session.queue(fakes.rows("c93bbbfd4279"))
    osm_session.queue(fakes.raises(OSError("connection refused")))
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["schema"] == {"task": "c93bbbfd4279", "osm": None}


async def test_health_check_reports_missing_version_table_as_null(client):
    """An empty or missing alembic_version (an unmigrated database) is null."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["schema"] == {"task": None, "osm": None}


async def test_health_check_times_out_a_hung_database(
    client, monkeypatch, task_session, osm_session
):
    """A database that never answers is reported as null instead of stalling."""

    async def hang(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(main, "_SCHEMA_REVISION_TIMEOUT", 0.01)
    monkeypatch.setattr(osm_session, "execute", hang)
    task_session.queue(fakes.rows("c93bbbfd4279"))
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["schema"] == {"task": "c93bbbfd4279", "osm": None}


async def test_root_redirects_to_docs(client):
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"
