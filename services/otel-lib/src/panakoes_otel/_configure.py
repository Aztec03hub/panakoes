"""`configure()` entrypoint that wires the OpenTelemetry pipeline.

Calling `configure(service_name="my-svc")` once during process startup
installs:

- A `TracerProvider` with a batch OTLP/gRPC span exporter
- A `MeterProvider` with a periodic OTLP/gRPC metric exporter
- A `LoggerProvider` with a batch OTLP/gRPC log exporter
- The W3C TraceContext + Baggage propagators as the global text map

When `OTEL_SDK_DISABLED=true`, NoOp providers are installed instead and
no exporters are constructed. This is the default posture for unit
tests and for local development where no collector is running.
"""

from __future__ import annotations

import os

from opentelemetry import _logs as otel_logs
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry._logs import NoOpLoggerProvider
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NoOpTracerProvider
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from panakoes_otel import _state
from panakoes_otel._error_capture import install_exception_capture

DEFAULT_ENDPOINT = "http://localhost:4317"

# Per-export timeout (seconds). OTel's default is 10s; we shorten it so a
# misconfigured collector or a paused dev environment fails fast and never
# blocks a service shutdown for longer than necessary.
_EXPORT_TIMEOUT_SECONDS = 5


def _is_sdk_disabled() -> bool:
    """Return True when `OTEL_SDK_DISABLED` is set to a truthy value.

    OpenTelemetry recommends matching the canonical "true"/"false"
    string convention, so we follow suit. Any other value is treated
    as disabled-equals-false to keep accidental typos from silently
    turning off telemetry in production.
    """
    return os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true"


def _resolve_endpoint(explicit: str | None) -> str:
    """Resolve the OTLP endpoint with explicit-arg > env > default precedence."""
    if explicit is not None:
        return explicit
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_ENDPOINT)


def _build_resource(service_name: str, environment: str) -> Resource:
    """Construct the OTel `Resource` carrying our identifying attributes.

    `service.namespace=panakoes` is constant; `service.version` reads
    from `SERVICE_VERSION` so deployment automation can stamp it without
    code changes; `deployment.environment` mirrors the caller's argument.
    """
    version = os.environ.get("SERVICE_VERSION", "0.0.0")
    return Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "panakoes",
            "service.version": version,
            "deployment.environment": environment,
        }
    )


def _install_propagators() -> None:
    """Register the W3C TraceContext + Baggage composite as the global text map.

    Using a composite makes it easy to layer additional propagators
    later (e.g. b3 for legacy systems) without changing the call site.
    """
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
        )
    )


def _configure_disabled(resolved_endpoint: str) -> None:
    """Install NoOp providers when `OTEL_SDK_DISABLED=true`.

    The NoOp providers satisfy the same API surfaces but never produce
    data and never call the network. We still install propagators so
    inbound trace context still flows correctly between services even
    when this particular service has telemetry disabled (useful for
    tests that exercise multi-service flows).
    """
    tracer_provider = NoOpTracerProvider()
    meter_provider = NoOpMeterProvider()
    logger_provider = NoOpLoggerProvider()
    otel_trace.set_tracer_provider(tracer_provider)
    otel_metrics.set_meter_provider(meter_provider)
    otel_logs.set_logger_provider(logger_provider)
    _install_propagators()
    _state.set_state(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
        resolved_endpoint=resolved_endpoint,
    )


def _configure_sdk(
    *,
    service_name: str,
    environment: str,
    resolved_endpoint: str,
) -> None:
    """Install full SDK providers with OTLP/gRPC exporters."""
    resource = _build_resource(service_name, environment)

    tracer_provider = TracerProvider(resource=resource)
    span_exporter = OTLPSpanExporter(
        endpoint=resolved_endpoint, insecure=True, timeout=_EXPORT_TIMEOUT_SECONDS
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    otel_trace.set_tracer_provider(tracer_provider)

    metric_exporter = OTLPMetricExporter(
        endpoint=resolved_endpoint, insecure=True, timeout=_EXPORT_TIMEOUT_SECONDS
    )
    metric_reader = PeriodicExportingMetricReader(metric_exporter)
    meter_provider = MeterProvider(
        resource=resource, metric_readers=[metric_reader]
    )
    otel_metrics.set_meter_provider(meter_provider)

    log_exporter = OTLPLogExporter(
        endpoint=resolved_endpoint, insecure=True, timeout=_EXPORT_TIMEOUT_SECONDS
    )
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    otel_logs.set_logger_provider(logger_provider)

    _install_propagators()
    _state.set_state(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
        resolved_endpoint=resolved_endpoint,
    )


def configure(
    service_name: str,
    environment: str = "dev",
    endpoint: str | None = None,
) -> None:
    """Configure OpenTelemetry providers for this Python process.

    Idempotent: a second call with the same arguments returns without
    re-initializing. For test scenarios that need to reconfigure, call
    `shutdown()` first to clear state.

    Args:
        service_name: The OTel `service.name` resource attribute.
        environment: Deployment environment ("dev", "staging", "prod"...).
        endpoint: Optional explicit OTLP/gRPC endpoint URL. Falls back
            to `OTEL_EXPORTER_OTLP_ENDPOINT` env var, then to
            `http://localhost:4317`.
    """
    if _state.is_configured():
        return
    resolved_endpoint = _resolve_endpoint(endpoint)
    if _is_sdk_disabled():
        _configure_disabled(resolved_endpoint)
        install_exception_capture()
        return
    _configure_sdk(
        service_name=service_name,
        environment=environment,
        resolved_endpoint=resolved_endpoint,
    )
    install_exception_capture()
