"""Module-level state for the configured OpenTelemetry providers.

Kept in a dedicated module so tests can import it cleanly via
`from panakoes_otel import _state` and inspect the providers without
relying on module-internal attribute lookups. The state holder pattern
also makes it trivial to reset between tests via `reset_for_tests`.
"""

from __future__ import annotations

from typing import Any

# These are stored as `Any` because they may be either real SDK provider
# instances or NoOp providers depending on `OTEL_SDK_DISABLED`. Both
# satisfy the API protocols the rest of the library uses.
_tracer_provider: Any | None = None
_meter_provider: Any | None = None
_logger_provider: Any | None = None
_resolved_endpoint: str | None = None
_configured: bool = False


def is_configured() -> bool:
    """Return True if `configure()` has installed providers since the last reset."""
    return _configured


def get_tracer_provider() -> Any:
    """Return the currently installed tracer provider.

    Tests call this to assert provider type (NoOp vs SDK). Returns None
    if `configure()` has not been called.
    """
    return _tracer_provider


def get_meter_provider() -> Any:
    """Return the currently installed meter provider."""
    return _meter_provider


def get_logger_provider() -> Any:
    """Return the currently installed logger provider."""
    return _logger_provider


def get_resolved_endpoint() -> str | None:
    """Return the endpoint URL that `configure()` resolved on its last call."""
    return _resolved_endpoint


def set_state(
    *,
    tracer_provider: Any,
    meter_provider: Any,
    logger_provider: Any,
    resolved_endpoint: str,
) -> None:
    """Persist the providers and resolved endpoint after a successful configure."""
    global _tracer_provider, _meter_provider, _logger_provider
    global _resolved_endpoint, _configured
    _tracer_provider = tracer_provider
    _meter_provider = meter_provider
    _logger_provider = logger_provider
    _resolved_endpoint = resolved_endpoint
    _configured = True


def clear_state() -> None:
    """Forget the installed providers without touching the OTel globals.

    `shutdown()` calls this after invoking each provider's own
    shutdown hook so subsequent `is_configured()` reports honestly.
    """
    global _tracer_provider, _meter_provider, _logger_provider
    global _resolved_endpoint, _configured
    _tracer_provider = None
    _meter_provider = None
    _logger_provider = None
    _resolved_endpoint = None
    _configured = False


def reset_for_tests() -> None:
    """Clear all module-level state. Tests call this in their fixtures."""
    clear_state()
