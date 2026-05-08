"""Shared OpenTelemetry instrumentation for Panakoes Python services.

Every Python microservice imports this package once, calls
`configure(service_name=...)` during process startup, and then optionally
calls `instrument_fastapi(app)`, `instrument_boto3()`, or
`instrument_httpx()` to enable automatic instrumentation for the
libraries it uses.

Resource attributes follow the OpenTelemetry semantic conventions:

- `service.name`: the consuming service's name (caller-supplied)
- `service.namespace`: always `panakoes`
- `service.version`: from the `SERVICE_VERSION` env var, defaulting to `0.0.0`
- `deployment.environment`: from the `environment` argument, default `dev`

Exporters speak OTLP/gRPC by default and dial the collector at
`OTEL_EXPORTER_OTLP_ENDPOINT` (env var) or `http://localhost:4317`. In
tests and offline development, set `OTEL_SDK_DISABLED=true` to wire
NoOp providers and skip every exporter so nothing tries to open a
network socket.
"""

from __future__ import annotations

from panakoes_otel._configure import configure
from panakoes_otel._getters import get_meter, get_tracer
from panakoes_otel._instrument import (
    instrument_boto3,
    instrument_fastapi,
    instrument_httpx,
)
from panakoes_otel._lifecycle import shutdown

__all__ = [
    "configure",
    "get_meter",
    "get_tracer",
    "instrument_boto3",
    "instrument_fastapi",
    "instrument_httpx",
    "shutdown",
]
