"""Correlation-ID middleware and helpers.

Every inbound request carries (or is assigned) a stable identifier
that flows through logs, downstream service calls, and audit events.
We accept the client-supplied `X-Request-Id` if present and well-formed
and otherwise mint a fresh ULID. The id is stashed in a contextvar so
deeply nested code (logging filters, audit decorators, background
tasks) can read it without threading the value through arguments.

A logging filter is exposed as `RequestIdLogFilter` so services can
attach it to their root logger and have every log record gain a
`request_id` attribute automatically.
"""

from __future__ import annotations

import contextvars
import logging
import secrets
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp


REQUEST_ID_HEADER = "X-Request-Id"
"""Canonical header name for inbound and outbound correlation IDs."""

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "panakoes_request_id",
    default=None,
)
"""Contextvar holding the request ID for the current execution context."""


# Crockford's base32 alphabet (excludes I, L, O, U to avoid look-alike chars).
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _generate_ulid() -> str:
    """Generate a fresh ULID string (Crockford base32, 26 chars).

    We avoid pulling in a separate `python-ulid` dependency for one
    helper. The format we emit is compatible with the spec: a 48-bit
    millisecond timestamp followed by 80 random bits, encoded as 26
    base32 characters.
    """
    timestamp_ms = int(time.time() * 1000)
    random_bits = secrets.randbits(80)
    combined = (timestamp_ms << 80) | random_bits
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD_ALPHABET[combined & 0x1F])
        combined >>= 5
    return "".join(reversed(chars))


def get_request_id() -> str:
    """Return the request ID for the current context, or `""` if unset.

    Handlers and helpers running inside a request can call this to
    correlate their work with the inbound request. Outside of a
    request context the returned value is the empty string, which is
    safe to interpolate into log lines without raising.
    """
    return _request_id_var.get() or ""


class RequestIdLogFilter(logging.Filter):
    """Logging filter that injects `request_id` into every record.

    Attach to your root logger (or to handlers individually) so log
    formatters can reference `%(request_id)s` without each log call
    having to pass it explicitly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Set `record.request_id` from the contextvar; always pass."""
        record.request_id = _request_id_var.get() or "-"
        return True


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Extract or generate a request ID and propagate it everywhere.

    The middleware reads `X-Request-Id` from the inbound request. If the
    header is missing or empty, a fresh ULID is generated. The id is:

    - stored in a contextvar for the duration of the request,
    - copied to `request.state.request_id` for handlers,
    - echoed back to the client in the same response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the ASGI app with correlation-id propagation."""
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Resolve the request ID, run the app, attach the response header."""
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming if incoming else _generate_ulid()
        token = _request_id_var.set(request_id)
        try:
            request.state.request_id = request_id
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id_var.reset(token)
