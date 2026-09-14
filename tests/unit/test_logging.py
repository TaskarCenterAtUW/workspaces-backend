"""Tests for the application's standard and OpenTelemetry logging setup."""

import logging

from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.semconv.resource import ResourceAttributes

from api.core import logging as app_logging


def test_setup_logging_defaults_to_info_without_otlp_endpoint(monkeypatch):
    monkeypatch.setattr(app_logging, "_logger_provider", None)
    monkeypatch.setattr(app_logging, "_otel_handler", None)
    monkeypatch.setattr(
        app_logging.settings,
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "",
    )
    monkeypatch.setattr(app_logging.settings, "DEBUG", False)

    app_logging.setup_logging()

    assert logging.getLogger().level == logging.INFO
    assert app_logging._logger_provider is None


def test_setup_logging_adds_single_otlp_handler(monkeypatch):
    monkeypatch.setattr(app_logging, "_logger_provider", None)
    monkeypatch.setattr(app_logging, "_otel_handler", None)
    monkeypatch.setattr(
        app_logging.settings,
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "http://collector.example/v1/logs",
    )
    monkeypatch.setattr(app_logging.settings, "OTEL_SERVICE_NAME", "test-service")

    app_logging.setup_logging()
    provider = app_logging._logger_provider
    handler = app_logging._otel_handler
    app_logging.setup_logging()

    assert provider is not None
    assert handler is not None
    assert app_logging._logger_provider is provider
    assert app_logging._otel_handler is handler
    assert (
        provider.resource.attributes[ResourceAttributes.SERVICE_NAME] == "test-service"
    )
    assert (
        sum(
            isinstance(root_handler, LoggingHandler)
            for root_handler in logging.getLogger().handlers
        )
        == 1
    )

    app_logging.shutdown_logging()
    assert app_logging._logger_provider is None
    assert app_logging._otel_handler is None
