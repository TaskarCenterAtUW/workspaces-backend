import asyncio
import json
from typing import Any, NoReturn

import httpx
import jsonschema
from fastapi import HTTPException, status

from api.core.config import settings

# Shared HTTP client. Initialized by main.py lifespan.
_http_client: httpx.AsyncClient | None = None

_longform_schema: dict | None = None
_longform_schema_lock = asyncio.Lock()

_imagery_schema: dict | None = None
_imagery_schema_lock = asyncio.Lock()

# Test outline:
# @test: Test that this class validates JSON payloads properly against the JSON schema fetched
# @test: Test that malformed JSON or JSON that doesn't validate 
# @test: Test that any failed network requests are handled gracefully and return a proper error (not just "Exception") or worse a false positive validation

def init_json_schema_client() -> None:
    global _http_client
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=30, write=30, pool=10),
    )


def get_http_client() -> httpx.AsyncClient | None:
    return _http_client


def _require_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Schema HTTP client is not initialized",
        )
    return _http_client


async def close_json_schema_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def _fetch_longform_schema() -> dict:
    global _longform_schema
    cached = _longform_schema
    if cached is not None:
        return cached
    async with _longform_schema_lock:
        if _longform_schema is None:
            response = await _require_http_client().get(settings.LONGFORM_SCHEMA_URL)
            response.raise_for_status()
            schema: dict = response.json()
            _longform_schema = schema
            return schema
        return _longform_schema


async def _fetch_imagery_schema() -> dict:
    global _imagery_schema
    cached = _imagery_schema
    if cached is not None:
        return cached
    async with _imagery_schema_lock:
        if _imagery_schema is None:
            response = await _require_http_client().get(settings.IMAGERY_SCHEMA_URL)
            response.raise_for_status()
            schema: dict = response.json()
            _imagery_schema = schema
            return schema
        return _imagery_schema


def _raise_for_fetch_error(e: Exception, label: str) -> NoReturn:
    if isinstance(e, httpx.TimeoutException):
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Timed out fetching {label} schema",
        )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Failed to fetch {label} schema: {e}",
    )


async def validate_quest_definition_schema(definition: str) -> None:
    """
    Parse, type-check, and validate a quest definition string against the long-
    form quest JSON schema.
    """
    try:
        parsed = json.loads(definition)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'definition' must be valid JSON: {e}",
        )
    if not parsed or not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'definition' must be a JSON object.",
        )

    try:
        schema = await _fetch_longform_schema()
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_fetch_error(e, "quest")

    try:
        jsonschema.validate(instance=parsed, schema=schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{e.message} at {list(e.path)}",
        )

async def validate_imagery_definition_schema(definition: list[Any]) -> None:
    """
    Validate the provided definition against the imagery list schema.
    """
    try:
        schema = await _fetch_imagery_schema()
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_fetch_error(e, "imagery")

    try:
        jsonschema.validate(instance=definition, schema=schema)
    except jsonschema.ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{e.message} at {list(e.path)}",
        )
