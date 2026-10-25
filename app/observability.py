"""Observability helpers: structured logging and OpenTelemetry tracing.

The module has no hard dependency on OpenTelemetry. When the packages are not
installed, tracing is disabled and the service keeps working; when they are
installed, spans are exported through the OTLP HTTP exporter when an endpoint
is configured.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

LOGGER = logging.getLogger("searchrelay")


class JsonLogFormatter(logging.Formatter):
    """Render log records as one JSON object per line."""

    _RESERVED = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = _json_safe(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def configure_logging(*, level: str = "INFO", json_logs: bool = True) -> None:
    """Install a single log handler on the root logger."""

    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
        )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn's own loggers through the same handler so the whole
    # process emits one log format. The access log is disabled because the
    # service emits its own structured access log line per request.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = [handler]
    access_logger.propagate = False
    access_logger.disabled = True


class TracingUnavailable(RuntimeError):
    """Raised when tracing is requested but the OpenTelemetry packages are absent."""


def setup_tracing(
    *,
    service_name: str = "searchrelay",
    enabled: bool = True,
    otlp_endpoint: str | None = None,
) -> Any | None:
    """Configure the OpenTelemetry tracer provider.

    Returns the tracer provider, or ``None`` when tracing is disabled. Raises
    :class:`TracingUnavailable` when tracing is enabled but the OpenTelemetry
    packages are not installed, so a misconfigured deployment fails fast
    instead of silently dropping spans.
    """

    if not enabled:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise TracingUnavailable(
            "Tracing is enabled but the OpenTelemetry packages are not installed. "
            "Install them with: pip install 'searchrelay[tracing]'"
        ) from exc

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise TracingUnavailable(
                "OTLP export is configured but opentelemetry-exporter-otlp-proto-http "
                "is not installed"
            ) from exc
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    else:
        LOGGER.info(
            "tracing_enabled_without_exporter",
            extra={"service_name": service_name},
        )
    trace.set_tracer_provider(provider)
    return provider


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a span when tracing is available and yield it either way."""

    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - depends on the environment
        yield None
        return
    tracer = trace.get_tracer("searchrelay")
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            try:
                current.set_attribute(key, value)
            except Exception:  # pragma: no cover - defensive, never break requests
                continue
        yield current
