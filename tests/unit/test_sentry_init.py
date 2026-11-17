"""Tests for `init_sentry` in api/main.py: it must say at startup whether
errors reach Sentry, since the SDK sends nothing, silently, without a DSN."""

import logging

import pytest

import api.main as main


@pytest.fixture
def sentry_calls(monkeypatch):
    # Record init/set_tag instead of reconfiguring the real SDK.
    calls: dict = {}
    monkeypatch.setattr(
        main.sentry_sdk, "init", lambda **kwargs: calls.update(init=kwargs)
    )
    monkeypatch.setattr(
        main.sentry_sdk, "set_tag", lambda key, value: calls.update(tag=(key, value))
    )
    return calls


def test_missing_dsn_logs_a_warning(monkeypatch, caplog, sentry_calls):
    monkeypatch.setattr(main.config.settings, "SENTRY_DSN", "")

    with caplog.at_level(logging.INFO, logger=main.logger.name):
        main.init_sentry()

    assert sentry_calls["init"]["dsn"] == ""
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert ["SENTRY_DSN is not set" in r.getMessage() for r in warnings] == [True]


def test_set_dsn_logs_environment_and_release(monkeypatch, caplog, sentry_calls):
    monkeypatch.setattr(
        main.config.settings, "SENTRY_DSN", "https://key@sentry.example/1"
    )
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CODE_VERSION", "abc123")

    with caplog.at_level(logging.INFO, logger=main.logger.name):
        main.init_sentry()

    assert sentry_calls["init"]["environment"] == "production"
    assert sentry_calls["init"]["release"] == "abc123"
    assert sentry_calls["tag"] == ("version", "abc123")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "environment='production', release='abc123'" in caplog.text
