"""Tests for `get_tracer` and `get_meter` convenience getters."""

from __future__ import annotations

import pytest
from opentelemetry.metrics import Meter
from opentelemetry.trace import Tracer

import panakoes_otel


@pytest.mark.unit
def test_get_tracer_returns_tracer_instance() -> None:
    """`get_tracer(name)` returns a `Tracer` bound to the configured provider."""
    panakoes_otel.configure(service_name="auth")
    tracer = panakoes_otel.get_tracer("panakoes_otel.tests")
    assert isinstance(tracer, Tracer)


@pytest.mark.unit
def test_get_meter_returns_meter_instance() -> None:
    """`get_meter(name)` returns a `Meter` bound to the configured provider."""
    panakoes_otel.configure(service_name="auth")
    meter = panakoes_otel.get_meter("panakoes_otel.tests")
    assert isinstance(meter, Meter)


@pytest.mark.unit
def test_get_tracer_works_before_configure() -> None:
    """Getting a tracer before configure returns a no-op tracer (proxy)."""
    tracer = panakoes_otel.get_tracer("panakoes_otel.tests")
    # ProxyTracer also implements Tracer; the call should never raise.
    assert tracer is not None


@pytest.mark.unit
def test_get_meter_works_before_configure() -> None:
    """Getting a meter before configure returns a no-op meter (proxy)."""
    meter = panakoes_otel.get_meter("panakoes_otel.tests")
    assert meter is not None
