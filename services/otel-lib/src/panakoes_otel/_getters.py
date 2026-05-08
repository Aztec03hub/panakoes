"""Convenience getters for tracers and meters.

Wrapping `opentelemetry.trace.get_tracer` / `opentelemetry.metrics.get_meter`
at the package level keeps consuming code provider-agnostic: services
import from `panakoes_otel` rather than reaching into the OTel API
directly. If we ever swap providers (e.g. add a custom decorator), the
change lands here.
"""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Tracer


def get_tracer(name: str) -> Tracer:
    """Return a `Tracer` for the named instrumentation scope.

    Calling before `configure()` is harmless: OTel returns a proxy
    tracer that no-ops until a real provider is installed.
    """
    return trace.get_tracer(name)


def get_meter(name: str) -> Meter:
    """Return a `Meter` for the named instrumentation scope.

    Same proxy semantics as `get_tracer`.
    """
    return metrics.get_meter(name)
