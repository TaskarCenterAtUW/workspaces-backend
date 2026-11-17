"""Integration smoke tests for unauthenticated endpoints."""


async def test_health_check(client, monkeypatch):
    monkeypatch.setenv("CODE_VERSION", "abc123")
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "abc123"}


async def test_health_check_without_code_version(client, monkeypatch):
    monkeypatch.delenv("CODE_VERSION", raising=False)
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "unknown"}


async def test_root_redirects_to_docs(client):
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"
