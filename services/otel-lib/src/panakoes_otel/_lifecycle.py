"""Process-shutdown helper for the panakoes-otel library.

Calling `shutdown()` flushes any buffered spans, metrics, and logs, then
clears the library's module-level state so a subsequent `configure()`
call re-initializes cleanly. Safe to call multiple times.
"""

from __future__ import annotations

from panakoes_otel import _state


def shutdown() -> None:
    """Flush exporters and tear down the configured providers.

    Should be invoked from a service's shutdown hook (FastAPI's
    lifespan exit, AWS Lambda's atexit, etc.) so in-flight spans and
    metrics are not lost. Idempotent.
    """
    if not _state.is_configured():
        return

    tracer_provider = _state.get_tracer_provider()
    meter_provider = _state.get_meter_provider()
    logger_provider = _state.get_logger_provider()

    # NoOp providers don't expose a `shutdown` method on every version,
    # so we guard the call. SDK providers always do.
    for provider in (tracer_provider, meter_provider, logger_provider):
        shutdown_fn = getattr(provider, "shutdown", None)
        if callable(shutdown_fn):
            shutdown_fn()

    _state.clear_state()
