"""Tests for `panakoes_otel.shutdown`: flush + idempotency."""

from __future__ import annotations

import pytest

import panakoes_otel
from panakoes_otel import _state


@pytest.mark.unit
def test_shutdown_clears_state() -> None:
    """After shutdown, the providers are released and the state resets."""
    panakoes_otel.configure(service_name="auth")
    assert _state.is_configured() is True
    panakoes_otel.shutdown()
    assert _state.is_configured() is False


@pytest.mark.unit
def test_shutdown_is_idempotent() -> None:
    """Calling shutdown multiple times does not raise."""
    panakoes_otel.configure(service_name="auth")
    panakoes_otel.shutdown()
    panakoes_otel.shutdown()


@pytest.mark.unit
def test_shutdown_without_configure_is_safe() -> None:
    """Calling shutdown before configure does not raise."""
    panakoes_otel.shutdown()


@pytest.mark.unit
def test_shutdown_calls_provider_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`shutdown()` invokes both the tracer and meter provider shutdown hooks."""
    panakoes_otel.configure(service_name="auth")
    tracer_provider = _state.get_tracer_provider()
    meter_provider = _state.get_meter_provider()
    tracer_calls: list[bool] = []
    meter_calls: list[bool] = []
    monkeypatch.setattr(
        tracer_provider, "shutdown", lambda: tracer_calls.append(True)
    )
    monkeypatch.setattr(
        meter_provider, "shutdown", lambda: meter_calls.append(True)
    )
    panakoes_otel.shutdown()
    assert tracer_calls == [True]
    assert meter_calls == [True]


@pytest.mark.unit
def test_shutdown_with_sdk_disabled_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SDK is disabled, shutdown still succeeds without error."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    panakoes_otel.configure(service_name="auth")
    panakoes_otel.shutdown()
    assert _state.is_configured() is False
