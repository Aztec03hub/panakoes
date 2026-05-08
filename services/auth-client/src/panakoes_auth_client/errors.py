"""Exception types raised by the auth-client library.

Two exceptions cover the public surface:

- `JwtInvalidError`: a token failed validation for any reason. Callers
  treat this as a single category and respond with HTTP 401; the
  underlying cause is intentionally not exposed to clients (echoing
  it leaks signal to attackers).
- `JwtConfigError`: the library was constructed with missing or empty
  configuration, almost always a deployment bug. Distinct from
  `JwtInvalidError` so it surfaces during startup, not on every request.
"""

from __future__ import annotations


class JwtInvalidError(Exception):
    """Raised when a token cannot be validated.

    Covers expired tokens, bad signatures, wrong issuer or audience,
    malformed tokens, and missing required claims. Callers should
    respond with HTTP 401; do not echo the message to the client.
    """


class JwtConfigError(Exception):
    """Raised when the library is missing required configuration.

    Examples: `from_env()` called with `JWT_SECRET` unset, an empty
    issuer, or an empty audience. This is a startup error, not a
    per-request error.
    """
