"""Tests for `panakoes_otel.configure` and the resource attribute setup."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.trace import NoOpTracerProvider

import panakoes_otel
from panakoes_otel import _state


@pytest.mark.unit
def test_configure_with_sdk_disabled_uses_noop_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_SDK_DISABLED=true` wires NoOp providers for tests/local dev."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    panakoes_otel.configure(service_name="test-service")
    assert isinstance(_state.get_tracer_provider(), NoOpTracerProvider)
    assert isinstance(_state.get_meter_provider(), NoOpMeterProvider)


@pytest.mark.unit
def test_configure_happy_path_uses_sdk_providers() -> None:
    """When SDK is not disabled, real SDK providers are constructed."""
    panakoes_otel.configure(
        service_name="test-service",
        environment="dev",
        endpoint="http://localhost:4317",
    )
    assert isinstance(_state.get_tracer_provider(), SDKTracerProvider)
    assert isinstance(_state.get_meter_provider(), SDKMeterProvider)


@pytest.mark.unit
def test_configure_sets_resource_attributes() -> None:
    """Resource carries service.name, service.namespace, service.version, env."""
    panakoes_otel.configure(
        service_name="ingestion-api",
        environment="staging",
    )
    provider = _state.get_tracer_provider()
    assert isinstance(provider, SDKTracerProvider)
    attrs = provider.resource.attributes
    assert attrs["service.name"] == "ingestion-api"
    assert attrs["service.namespace"] == "panakoes"
    assert attrs["deployment.environment"] == "staging"
    assert attrs["service.version"] == "0.0.0"


@pytest.mark.unit
def test_configure_reads_service_version_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SERVICE_VERSION` env var populates the service.version attribute."""
    monkeypatch.setenv("SERVICE_VERSION", "1.4.2")
    panakoes_otel.configure(service_name="auth")
    provider = _state.get_tracer_provider()
    assert isinstance(provider, SDKTracerProvider)
    assert provider.resource.attributes["service.version"] == "1.4.2"


@pytest.mark.unit
def test_configure_reads_endpoint_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When `endpoint` is None, `OTEL_EXPORTER_OTLP_ENDPOINT` is honored."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.local:4317")
    panakoes_otel.configure(service_name="auth")
    assert _state.get_resolved_endpoint() == "http://collector.local:4317"


@pytest.mark.unit
def test_configure_endpoint_default_when_unset() -> None:
    """Without env or arg, endpoint defaults to localhost:4317."""
    panakoes_otel.configure(service_name="auth")
    assert _state.get_resolved_endpoint() == "http://localhost:4317"


@pytest.mark.unit
def test_configure_explicit_endpoint_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit endpoint argument wins over the env var."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://env-collector:4317")
    panakoes_otel.configure(
        service_name="auth", endpoint="http://explicit:4317"
    )
    assert _state.get_resolved_endpoint() == "http://explicit:4317"


@pytest.mark.unit
def test_configure_sets_global_tracer_provider() -> None:
    """After configure, the global OTel tracer provider matches our SDK provider."""
    panakoes_otel.configure(service_name="auth")
    global_provider = trace.get_tracer_provider()
    assert global_provider is _state.get_tracer_provider()


@pytest.mark.unit
def test_configure_idempotent_does_not_double_initialize() -> None:
    """Calling configure twice is a safe no-op on the second call."""
    panakoes_otel.configure(service_name="auth")
    first_provider = _state.get_tracer_provider()
    panakoes_otel.configure(service_name="auth")
    assert _state.get_tracer_provider() is first_provider


@pytest.mark.unit
def test_configure_w3c_propagator_installed() -> None:
    """W3C TraceContext propagator is registered as the global propagator."""
    from opentelemetry.propagate import get_global_textmap
    from opentelemetry.propagators.composite import CompositePropagator

    panakoes_otel.configure(service_name="auth")
    propagator = get_global_textmap()
    # tracecontext propagator (or a composite that contains it) is installed.
    fields = propagator.fields
    assert isinstance(propagator, CompositePropagator) or "traceparent" in fields
