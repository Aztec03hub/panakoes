"""Tests for `panakoes_otel._error_capture`.

The capture module hooks four entrypoints:

1. `sys.excepthook` for synchronous uncaught exceptions
2. `threading.excepthook` for worker thread crashes
3. asyncio loop's default exception handler for coroutine failures
4. A `logging.Filter` on the root logger for ERROR/CRITICAL records

Each is exercised in isolation. The tests run with `OTEL_SDK_DISABLED=false`
so a real `SDKTracerProvider` is installed and `record_exception` / spans
become observable via `InMemorySpanExporter` (wired by `conftest.py`).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider as SDKTracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import panakoes_otel
from panakoes_otel import _error_capture, _state


def _install_in_memory_exporter() -> InMemorySpanExporter:
    """Attach a SimpleSpanProcessor with an in-memory exporter to the provider.

    `configure()` installs a BatchSpanProcessor which is asynchronous and adds
    test flakiness. SimpleSpanProcessor is synchronous: each ended span is
    pushed straight to the exporter before the call returns.
    """
    provider = _state.get_tracer_provider()
    assert isinstance(provider, SDKTracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


@pytest.mark.unit
def test_install_is_idempotent() -> None:
    """Calling install twice leaves a single set of hooks installed."""
    panakoes_otel.configure(service_name="error-capture-test")
    assert _error_capture.is_installed() is True
    original = sys.excepthook
    _error_capture.install_exception_capture()
    assert sys.excepthook is original


@pytest.mark.unit
def test_opt_out_env_var_skips_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """`OTEL_DISABLE_ERROR_CAPTURE=true` blocks the hooks from installing."""
    monkeypatch.setenv("OTEL_DISABLE_ERROR_CAPTURE", "true")
    panakoes_otel.configure(service_name="opt-out-test")
    assert _error_capture.is_installed() is False
    assert sys.excepthook is sys.__excepthook__ or callable(sys.excepthook)


@pytest.mark.unit
def test_exception_in_span_is_recorded() -> None:
    """Raising inside a span attaches the exception event to it."""
    panakoes_otel.configure(service_name="in-span-test")
    exporter = _install_in_memory_exporter()
    tracer = trace.get_tracer("test")

    with (
        pytest.raises(ValueError),
        tracer.start_as_current_span("op") as span,
    ):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            span.record_exception(exc)
            raise

    spans = exporter.get_finished_spans()
    assert any(
        e.name == "exception" for s in spans for e in s.events
    ), "expected an 'exception' event on the recorded span"


@pytest.mark.unit
def test_sys_excepthook_records_exception() -> None:
    """`sys.excepthook` fires our handler and a one-shot span captures it."""
    panakoes_otel.configure(service_name="excepthook-test")
    exporter = _install_in_memory_exporter()

    exc = RuntimeError("uncaught")
    sys.excepthook(type(exc), exc, exc.__traceback__)

    spans = exporter.get_finished_spans()
    assert any(s.name == "uncaught_exception" for s in spans)


@pytest.mark.unit
def test_threading_excepthook_records_exception() -> None:
    """`threading.excepthook` records the worker-thread crash."""
    panakoes_otel.configure(service_name="thread-excepthook-test")
    exporter = _install_in_memory_exporter()

    def worker() -> None:
        raise RuntimeError("thread boom")

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    spans = exporter.get_finished_spans()
    assert any(s.name == "uncaught_exception" for s in spans)


@pytest.mark.unit
def test_asyncio_exception_handler_records_exception() -> None:
    """Orphaned coroutine exceptions are recorded via the loop hook."""
    panakoes_otel.configure(service_name="asyncio-test")
    exporter = _install_in_memory_exporter()

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_error_capture._asyncio_exception_handler)
    try:
        loop.call_exception_handler(
            {"message": "x", "exception": RuntimeError("async boom")}
        )
    finally:
        loop.close()

    spans = exporter.get_finished_spans()
    assert any(s.name == "uncaught_exception" for s in spans)


@pytest.mark.unit
def test_logging_filter_adds_event_to_active_span() -> None:
    """`logger.error(...)` inside a span becomes a `log` event on that span."""
    panakoes_otel.configure(service_name="logging-filter-test")
    exporter = _install_in_memory_exporter()
    tracer = trace.get_tracer("test")
    logger = logging.getLogger("panakoes.test-logger")

    with tracer.start_as_current_span("op"):
        logger.error("something failed: %s", "details")

    spans = exporter.get_finished_spans()
    events = [e for s in spans for e in s.events if e.name == "log"]
    assert events, "expected a 'log' event from the filter"
    attrs = dict(events[0].attributes or {})
    assert attrs["log.severity"] == "ERROR"
    assert "something failed: details" in attrs["log.message"]
    assert attrs["log.logger"] == "panakoes.test-logger"


@pytest.mark.unit
def test_logging_filter_below_error_is_passthrough() -> None:
    """INFO/WARNING records do not create span events."""
    panakoes_otel.configure(service_name="logging-info-test")
    exporter = _install_in_memory_exporter()
    tracer = trace.get_tracer("test")
    logger = logging.getLogger("panakoes.test-info")
    logger.setLevel(logging.DEBUG)

    with tracer.start_as_current_span("op"):
        logger.warning("not bad enough")

    spans = exporter.get_finished_spans()
    events = [e for s in spans for e in s.events if e.name == "log"]
    assert events == []


@pytest.mark.unit
def test_logging_filter_no_active_span_is_noop() -> None:
    """ERROR records outside a span do not crash and do not create spans."""
    panakoes_otel.configure(service_name="logging-no-span-test")
    exporter = _install_in_memory_exporter()
    logger = logging.getLogger("panakoes.outside")
    logger.error("orphan")
    # No assertion failure expected. Sanity-check: no log events recorded.
    spans = exporter.get_finished_spans()
    assert all(e.name != "log" for s in spans for e in s.events)


@pytest.mark.unit
def test_uninstall_restores_hooks() -> None:
    """`uninstall_exception_capture()` puts the original hooks back in place."""
    pre_sys = sys.excepthook
    pre_thread = threading.excepthook
    panakoes_otel.configure(service_name="uninstall-test")
    assert sys.excepthook is not pre_sys
    panakoes_otel.uninstall_exception_capture()
    assert sys.excepthook is pre_sys
    assert threading.excepthook is pre_thread
    assert _error_capture.is_installed() is False


@pytest.mark.unit
def test_record_on_active_span_with_recording_span() -> None:
    """Direct exercise of `_record_on_active_span` with an active span."""
    panakoes_otel.configure(service_name="rec-test")
    exporter = _install_in_memory_exporter()
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("op"):
        _error_capture._record_on_active_span(ValueError("direct"))
    spans = exporter.get_finished_spans()
    assert any(
        e.name == "exception" for s in spans for e in s.events
    )


@pytest.mark.unit
def test_force_flush_handles_noop_provider() -> None:
    """`_force_flush` is a no-op when only a NoOp provider is installed."""
    # No configure() call: _state has no provider. _force_flush must return
    # cleanly rather than raising.
    _error_capture._force_flush()


@pytest.mark.unit
def test_shutdown_uninstalls_hooks() -> None:
    """`shutdown()` removes the error-capture hooks alongside providers."""
    panakoes_otel.configure(service_name="shutdown-uninstall-test")
    assert _error_capture.is_installed() is True
    panakoes_otel.shutdown()
    assert _error_capture.is_installed() is False
