import logging
import sys

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from api.core.config import settings

_logger_provider: LoggerProvider | None = None
_otel_handler: LoggingHandler | None = None

# @test: Standard application logs default to INFO level unless DEBUG is enabled.
# @test: When an OTLP logs endpoint is configured, log records are forwarded with the service name resource.
# @test: Repeated setup calls do not add duplicate OpenTelemetry handlers.


def setup_logging() -> None:
    """Set up local logging and, when configured, OTLP log export."""
    global _logger_provider, _otel_handler

    format_string = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    logging.basicConfig(
        level=level,
        format=format_string,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger().setLevel(level)

    if _logger_provider is not None or not settings.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT:
        return

    resource = Resource.create({SERVICE_NAME: settings.OTEL_SERVICE_NAME})
    _logger_provider = LoggerProvider(resource=resource)
    exporter = OTLPLogExporter(endpoint=settings.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT)
    _logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    _otel_handler = LoggingHandler(
        level=logging.NOTSET,
        logger_provider=_logger_provider,
    )
    logging.getLogger().addHandler(_otel_handler)


def shutdown_logging() -> None:
    """Flush and close the configured OpenTelemetry logging provider."""
    global _logger_provider, _otel_handler

    if _logger_provider is None:
        return

    if _otel_handler is not None:
        logging.getLogger().removeHandler(_otel_handler)
        _otel_handler = None
    _logger_provider.force_flush()
    _logger_provider.shutdown()
    _logger_provider = None


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
