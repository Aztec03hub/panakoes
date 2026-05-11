"""Capture uncaught exceptions and `logging.ERROR` records as span events.

`install_exception_capture()` registers four hooks so a service never has to
write `span.record_exception(exc)` by hand for an unhandled failure:

1. `sys.excepthook` for synchronous uncaught exceptions (main thread).
2. `threading.excepthook` for uncaught exceptions in worker threads.
3. `asyncio` loop `default_exception_handler` for orphaned coroutine errors.
4. A `logging` filter (NOT a handler) that intercepts `ERROR` and `CRITICAL`
   records on the root logger so they become events on whatever span is
   currently active. Filters work across every handler the consuming service
   attaches; a handler-based hook would silently miss services that route
   their logs through a custom handler.

Why a filter, not a handler:
- Handlers run only for records that survive every preceding filter. Filters
  see every record once and can opt to drop or pass it on. We want to observe
  every ERROR+ record regardless of formatter/handler topology, and we return
  True so the original handlers still see and emit the record.

The capture is opt-out via `OTEL_DISABLE_ERROR_CAPTURE=true` for tests that
intentionally throw. Default is on because errors going untracked is strictly
worse than test noise.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import threading
import types
from typing import Any

from opentelemetry import trace

from panakoes_otel import _state

_ENV_DISABLE = "OTEL_DISABLE_ERROR_CAPTURE"

# Module-level flags to keep install_exception_capture idempotent across calls
# (configure() is idempotent; this matches the same contract). We stash the
# original hooks so uninstall_exception_capture() can restore them in tests.
_installed: bool = False
_original_sys_excepthook: Any = None
_original_threading_excepthook: Any = None
_logging_filter: logging.Filter | None = None
_original_logger_handle: Any = None


def _is_disabled() -> bool:
    """Honor `OTEL_DISABLE_ERROR_CAPTURE=true` (canonical OTel-style flag)."""
    return os.environ.get(_ENV_DISABLE, "").lower() == "true"


def _force_flush() -> None:
    """Best-effort flush of the tracer provider; safe on NoOp providers."""
    tracer_provider = _state.get_tracer_provider()
    if tracer_provider is None:
        return
    force_flush = getattr(tracer_provider, "force_flush", None)
    if callable(force_flush):
        # Never let telemetry teardown mask the underlying crash.
        with contextlib.suppress(Exception):
            force_flush()


def _record_on_active_span(exc: BaseException) -> None:
    """Attach `exc` to the current span if one exists.

    No-op when there is no recording span (NoOp tracer, or the failure
    fired outside a span context). The hook for these cases falls back to
    a one-shot span so the failure is never silently dropped.
    """
    span = trace.get_current_span()
    if span is None or not span.is_recording():
        tracer = trace.get_tracer("panakoes-otel.error-capture")
        with tracer.start_as_current_span("uncaught_exception") as one_shot:
            one_shot.record_exception(exc)
            one_shot.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
        return
    span.record_exception(exc)
    span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))


def _sys_excepthook(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: types.TracebackType | None,
) -> None:
    """Replacement `sys.excepthook` that records then re-delegates."""
    try:
        _record_on_active_span(exc)
        _force_flush()
    finally:
        if _original_sys_excepthook is not None:
            _original_sys_excepthook(exc_type, exc, tb)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """Replacement `threading.excepthook` for worker thread crashes."""
    try:
        if args.exc_value is not None:
            _record_on_active_span(args.exc_value)
            _force_flush()
    finally:
        if _original_threading_excepthook is not None:
            _original_threading_excepthook(args)


def _asyncio_exception_handler(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, Any],
) -> None:
    """asyncio loop exception handler that records exc + delegates to default."""
    exc = context.get("exception")
    if isinstance(exc, BaseException):
        _record_on_active_span(exc)
        _force_flush()
    # Delegate to the loop's default handler so the message still prints.
    loop.default_exception_handler(context)


class _SpanEventLoggingFilter(logging.Filter):
    """logging filter that mirrors ERROR+ records as events on the active span.

    Returns True for every record so the host application's handlers still
    receive and emit it normally. This is observation-only.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        span = trace.get_current_span()
        if span is not None and span.is_recording():
            # Never let telemetry break the host logger.
            with contextlib.suppress(Exception):
                span.add_event(
                    "log",
                    attributes={
                        "log.severity": record.levelname,
                        "log.message": record.getMessage(),
                        "log.logger": record.name,
                    },
                )
        return True


def install_exception_capture() -> None:
    """Wire all four error-capture hooks. Idempotent. Honors opt-out env var.

    Called automatically by `configure()` so consuming services get error
    capture for free. Tests can flip `OTEL_DISABLE_ERROR_CAPTURE=true` to
    skip it when they intentionally raise.
    """
    global _installed, _original_sys_excepthook, _original_threading_excepthook
    global _logging_filter
    if _installed:
        return
    if _is_disabled():
        return

    _original_sys_excepthook = sys.excepthook
    sys.excepthook = _sys_excepthook

    _original_threading_excepthook = threading.excepthook
    threading.excepthook = _threading_excepthook

    # Install asyncio handler on the running loop if one exists; otherwise
    # set it on the policy so newly-created loops inherit it. Most services
    # call configure() before starting their event loop, so the policy path
    # is the common case.
    try:
        running_loop = asyncio.get_running_loop()
        running_loop.set_exception_handler(_asyncio_exception_handler)
    except RuntimeError:
        # No running loop; register on the policy's current loop if one
        # already exists. Services that start their loop later will still get
        # the excepthook + threading hooks; the asyncio one degrades to no-op
        # in that path. The alternative is monkeypatching the loop factory
        # which is more invasive than the win is worth.
        with contextlib.suppress(Exception):
            loop = asyncio.get_event_loop_policy().get_event_loop()
            loop.set_exception_handler(_asyncio_exception_handler)

    # Install the filter on the root logger so it shows up in `getFilters()`
    # for introspection, and ALSO patch `Logger.handle` so the filter runs
    # for records emitted on every descendant logger. Filters on a Logger
    # only fire for records originating at that Logger; they don't run on
    # records that merely propagate up to it. Patching `handle` once gives
    # us "filter across every handler/logger" semantics without registering
    # any new logging handler (which the brief explicitly forbade).
    _logging_filter = _SpanEventLoggingFilter()
    logging.getLogger().addFilter(_logging_filter)

    global _original_logger_handle
    _original_logger_handle = logging.Logger.handle

    def _patched_handle(self: logging.Logger, record: logging.LogRecord) -> None:
        if _logging_filter is not None:
            _logging_filter.filter(record)
        _original_logger_handle(self, record)

    logging.Logger.handle = _patched_handle  # type: ignore[method-assign]

    _installed = True


def uninstall_exception_capture() -> None:
    """Restore the prior hooks. Tests use this to keep fixtures clean."""
    global _installed, _original_sys_excepthook, _original_threading_excepthook
    global _logging_filter, _original_logger_handle
    if not _installed:
        return
    if _original_sys_excepthook is not None:
        sys.excepthook = _original_sys_excepthook
    if _original_threading_excepthook is not None:
        threading.excepthook = _original_threading_excepthook
    if _logging_filter is not None:
        logging.getLogger().removeFilter(_logging_filter)
    if _original_logger_handle is not None:
        logging.Logger.handle = _original_logger_handle  # type: ignore[method-assign]
    _original_sys_excepthook = None
    _original_threading_excepthook = None
    _logging_filter = None
    _original_logger_handle = None
    _installed = False


def is_installed() -> bool:
    """Return True when capture hooks are currently active."""
    return _installed
