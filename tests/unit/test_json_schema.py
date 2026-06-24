"""Tests for api/core/json_schema.py.

Covers the @test comments:
- payloads are validated against the fetched JSON schema
- malformed / non-object payloads return a 400 (not a generic Exception)
- network failures map to a proper 502/504 and never produce a false-positive
  (i.e. a fetch failure must raise, never silently "pass" validation)
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import api.core.json_schema as js

# --- validate_quest_definition_schema -------------------------------------


async def test_quest_valid_definition_passes(monkeypatch):
    async def fake_schema():
        return {"type": "object", "required": ["x"]}

    monkeypatch.setattr(js, "_fetch_longform_schema", fake_schema)

    # Should not raise.
    await js.validate_quest_definition_schema('{"x": 1}')


async def test_quest_schema_violation_returns_400(monkeypatch):
    async def fake_schema():
        return {"type": "object", "required": ["x"]}

    monkeypatch.setattr(js, "_fetch_longform_schema", fake_schema)

    with pytest.raises(HTTPException) as exc:
        await js.validate_quest_definition_schema('{"y": 1}')
    assert exc.value.status_code == 400


async def test_quest_malformed_json_returns_400():
    with pytest.raises(HTTPException) as exc:
        await js.validate_quest_definition_schema("{not valid json")
    assert exc.value.status_code == 400


async def test_quest_non_object_returns_400():
    with pytest.raises(HTTPException) as exc:
        await js.validate_quest_definition_schema("[1, 2, 3]")
    assert exc.value.status_code == 400


async def test_quest_fetch_timeout_maps_to_504(monkeypatch):
    async def boom():
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(js, "_fetch_longform_schema", boom)

    with pytest.raises(HTTPException) as exc:
        await js.validate_quest_definition_schema('{"x": 1}')
    assert exc.value.status_code == 504


async def test_quest_fetch_connect_error_maps_to_502(monkeypatch):
    async def boom():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(js, "_fetch_longform_schema", boom)

    with pytest.raises(HTTPException) as exc:
        await js.validate_quest_definition_schema('{"x": 1}')
    assert exc.value.status_code == 502


# --- validate_imagery_definition_schema -----------------------------------


async def test_imagery_valid_passes(monkeypatch):
    async def fake_schema():
        return {"type": "array", "items": {"type": "string"}}

    monkeypatch.setattr(js, "_fetch_imagery_schema", fake_schema)

    await js.validate_imagery_definition_schema(["a", "b"])


async def test_imagery_violation_returns_400(monkeypatch):
    async def fake_schema():
        return {"type": "array", "items": {"type": "string"}}

    monkeypatch.setattr(js, "_fetch_imagery_schema", fake_schema)

    with pytest.raises(HTTPException) as exc:
        await js.validate_imagery_definition_schema([1, 2])
    assert exc.value.status_code == 400


async def test_imagery_fetch_error_maps_to_502(monkeypatch):
    async def boom():
        raise httpx.ConnectError("down")

    monkeypatch.setattr(js, "_fetch_imagery_schema", boom)

    with pytest.raises(HTTPException) as exc:
        await js.validate_imagery_definition_schema([])
    assert exc.value.status_code == 502


# --- client + caching ------------------------------------------------------


def test_require_http_client_raises_503_when_uninitialized(monkeypatch):
    monkeypatch.setattr(js, "_http_client", None)
    with pytest.raises(HTTPException) as exc:
        js._require_http_client()
    assert exc.value.status_code == 503


async def test_fetch_longform_schema_caches_result(monkeypatch):
    monkeypatch.setattr(js, "_longform_schema", None)
    calls = {"n": 0}

    class FakeClient:
        async def get(self, url):
            calls["n"] += 1
            return SimpleNamespace(
                raise_for_status=lambda: None, json=lambda: {"fetched": True}
            )

    monkeypatch.setattr(js, "_http_client", FakeClient())

    first = await js._fetch_longform_schema()
    second = await js._fetch_longform_schema()

    assert first == second == {"fetched": True}
    assert calls["n"] == 1  # second call served from cache
