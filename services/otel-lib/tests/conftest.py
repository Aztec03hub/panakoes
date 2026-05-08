"""Shared pytest fixtures for the panakoes-otel library.

Two guarantees this conftest provides for every test:

1. OTEL_* env vars are scrubbed and library state is fully reset, so
   each test starts from a clean baseline and OTel's `_*_SET_ONCE`
   guard flags are cleared (they normally fire-then-warn on reuse).
2. The OTLP/gRPC exporters never open a real socket. The `_configure`
   module is patched to swap the OTLP exporter classes for in-memory
   stand-ins, so tests cover the SDK code path without a live
   collector and without any 5-10s connection timeout overhead.

The exporter monkey-patch is a focused, well-scoped substitution: we
only swap the three exporter constructors that `_configure_sdk` reads,
and we restore them when the test session ends. Tests that want to
exercise the *real* exporter constructors are out of scope for unit
coverage; that work belongs in an integration suite against a real
collector.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import _logs as otel_logs
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

import panakoes_otel
from panakoes_otel import _configure, _state


class _StubMetricExporter(MetricExporter):
    """No-op metric exporter used in unit tests to avoid network calls.

    Accepts and ignores any kwargs the real `OTLPMetricExporter` would
    take (endpoint, insecure, timeout, etc.).
    """

    def __init__(self, **_: Any) -> None:
        super().__init__()

    def export(self, metrics_data: Any, timeout_millis: float = 10_000) -> Any:
        from opentelemetry.sdk.metrics.export import MetricExportResult

        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        return None


class _StubSpanExporter(InMemorySpanExporter):
    """`InMemorySpanExporter` that ignores OTLP-shaped constructor kwargs."""

    def __init__(self, **_: Any) -> None:
        super().__init__()


class _StubLogExporter(InMemoryLogRecordExporter):
    """`InMemoryLogRecordExporter` that ignores OTLP-shaped constructor kwargs."""

    def __init__(self, **_: Any) -> None:
        super().__init__()

OTEL_ENV_KEYS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SDK_DISABLED",
    "SERVICE_VERSION",
)


def _reset_otel_globals() -> None:
    """Reset the OTel global provider singletons so tests can reconfigure."""
    otel_trace._TRACER_PROVIDER = None
    otel_trace._TRACER_PROVIDER_SET_ONCE = Once()
    otel_trace._PROXY_TRACER_PROVIDER = otel_trace.ProxyTracerProvider()

    otel_metrics._internal._METER_PROVIDER = None
    otel_metrics._internal._METER_PROVIDER_SET_ONCE = Once()
    otel_metrics._internal._PROXY_METER_PROVIDER = (
        otel_metrics._internal._ProxyMeterProvider()
    )

    otel_logs._internal._LOGGER_PROVIDER = None
    otel_logs._internal._LOGGER_PROVIDER_SET_ONCE = Once()
    otel_logs._internal._PROXY_LOGGER_PROVIDER = (
        otel_logs._internal.ProxyLoggerProvider()
    )


@pytest.fixture(autouse=True)
def clean_otel_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove OTEL_* env vars, reset library + global state, stub exporters."""
    for key in OTEL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # Swap OTLP exporters for in-memory or no-op equivalents so unit tests
    # never open a gRPC connection or wait for a 5-10s timeout.
    monkeypatch.setattr(_configure, "OTLPSpanExporter", _StubSpanExporter)
    monkeypatch.setattr(_configure, "OTLPLogExporter", _StubLogExporter)
    monkeypatch.setattr(_configure, "OTLPMetricExporter", _StubMetricExporter)
    panakoes_otel.shutdown()
    _state.reset_for_tests()
    _reset_otel_globals()
    yield
    panakoes_otel.shutdown()
    _state.reset_for_tests()
    _reset_otel_globals()
