"""Backend-agnostic error hierarchy for the Transcriber abstraction.

Every concrete backend maps its native errors into one of these types
so call sites can route on type without inspecting backend-specific
exception classes.
"""

from __future__ import annotations


class TranscriberError(Exception):
    """Base for every error raised by a Transcriber backend."""


class TranscriberAuthError(TranscriberError):
    """Backend rejected the request as unauthenticated or unauthorized.

    Typically a missing, revoked, or wrong API key. Caller surfaces to
    the operator; do not retry without rotating credentials.
    """


class TranscriberRateLimitError(TranscriberError):
    """Backend reported a rate-limit (HTTP 429 or equivalent).

    ``retry_after_seconds`` is parsed from the upstream's ``Retry-After``
    header when present; ``None`` otherwise. Caller may retry with
    backoff or route to a different backend.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        """Carry the optional ``Retry-After`` value alongside the message."""
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TranscriberUpstreamError(TranscriberError):
    """Backend reported an internal error (HTTP 5xx or equivalent).

    Often transient. Caller may retry with backoff; routers may
    circuit-break on repeated occurrences.
    """


class TranscriberTimeoutError(TranscriberError):
    """The backend call exceeded its configured timeout."""


class TranscriberRequestError(TranscriberError):
    """Backend rejected the request as invalid (HTTP 4xx other than 401/429).

    Examples: unsupported audio format, file too large, malformed
    parameters. Caller surfaces to the user; do not retry.
    """
