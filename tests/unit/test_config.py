"""Tests for api/core/config.py Settings.

Covers the @test comments on the Settings class:
- env vars of the same name override the members
- unset env vars fall back to declared defaults
- strings / numbers / empty strings / URLs all load as the defaults exemplify
"""

from api.core.config import Settings


def test_defaults_loaded_when_env_unset():
    # _env_file=None ignores any local .env so we observe the declared defaults.
    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg

    assert s.PROJECT_NAME == "Workspaces API"
    assert s.CORS_ORIGINS == ""
    assert s.DEBUG is False
    assert s.SENTRY_DSN == ""
    assert s.WS_OSM_HOST == "http://osm-web"
    assert s.TDEI_OIDC_REALM == "tdei"
    assert s.TASK_DATABASE_URL.startswith("postgresql+asyncpg://")
    assert s.OSM_DATABASE_URL.startswith("postgresql+asyncpg://")


def test_env_vars_override_members(monkeypatch):
    monkeypatch.setenv("PROJECT_NAME", "Custom Name")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("WS_OSM_HOST", "http://osm.example")
    monkeypatch.setenv("SENTRY_DSN", "https://sentry.example/123")

    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg

    assert s.PROJECT_NAME == "Custom Name"
    assert s.DEBUG is True
    assert s.WS_OSM_HOST == "http://osm.example"
    assert s.SENTRY_DSN == "https://sentry.example/123"


def test_cors_origins_parsed_from_json_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")

    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg

    assert s.CORS_ORIGINS == "https://a.example,https://b.example"


def test_value_types_and_formats():
    s = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings init kwarg

    assert isinstance(s.PROJECT_NAME, str)
    assert isinstance(s.CORS_ORIGINS, str)
    assert isinstance(s.DEBUG, bool)
    # empty-string default is preserved (not coerced to None):
    assert s.SENTRY_DSN == ""
    # URL-shaped defaults load verbatim:
    assert s.TDEI_BACKEND_URL.startswith("https://")
    assert s.LONGFORM_SCHEMA_URL.startswith("https://")
    assert s.IMAGERY_SCHEMA_URL.startswith("https://")
