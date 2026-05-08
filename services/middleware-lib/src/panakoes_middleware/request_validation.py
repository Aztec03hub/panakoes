"""Request-validation middlewares (size, required headers).

These middlewares enforce hygiene at the edge so service handlers can
trust the request shape. Both reject early with a JSON error body so
clients get a uniform failure mode regardless of which gate caught
them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp


DEFAULT_MAX_BYTES = 10 * 1024 * 1024
"""Default max request body size in bytes (10 MiB)."""


class MaxRequestSizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared body exceeds `max_bytes`.

    The check uses the `Content-Length` header, which clients are
    required to send for fixed-size bodies. Chunked uploads without a
    `Content-Length` are passed through; services that accept them
    should layer a streaming size guard inside the handler.

    Returning 413 (Payload Too Large) before reading the body is the
    cheap defense against memory-exhaustion attacks.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        """Wrap the ASGI app and remember the byte ceiling."""
        super().__init__(app)
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        """Return the configured size ceiling for callers and tests."""
        return self._max_bytes

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Reject oversized requests; otherwise hand off to the app."""
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header"},
                )
            if declared > self._max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        return await call_next(request)


class RequiredHeadersMiddleware(BaseHTTPMiddleware):
    """Reject requests that omit any header in `required`.

    Header names are matched case-insensitively (per RFC 7230). The
    rejection body lists the missing names so the client can fix
    their request without guessing.
    """

    def __init__(self, app: ASGIApp, required: list[str]) -> None:
        """Wrap the ASGI app and remember the required header names."""
        super().__init__(app)
        self._required = [name.lower() for name in required]

    @property
    def required(self) -> list[str]:
        """Return the lowercased required-header list."""
        return list(self._required)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Reject if any required header is absent; otherwise pass through."""
        present = {key.lower() for key in request.headers}
        missing = [name for name in self._required if name not in present]
        if missing:
            return JSONResponse(
                status_code=400,
                content={"detail": "Missing required headers", "missing": missing},
            )
        return await call_next(request)
