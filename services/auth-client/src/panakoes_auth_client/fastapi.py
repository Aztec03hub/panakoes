"""FastAPI dependency factory.

The factory returns a callable suitable for `Depends(...)` that
extracts the bearer token from the `Authorization` header, runs it
through the supplied `JwtValidator`, and returns the parsed
`JwtClaims`. Every failure (missing header, malformed scheme,
invalid token) maps to `HTTPException(401)` with the standard
`WWW-Authenticate: Bearer` challenge. The underlying error message
is intentionally not propagated to the client.

`fastapi` is an optional dependency. The import is local to the
factory body so importing the rest of the library on a service
without FastAPI installed does not fail.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from panakoes_auth_client.claims import JwtClaims
from panakoes_auth_client.errors import JwtInvalidError
from panakoes_auth_client.validator import JwtValidator

_BEARER_RE: re.Pattern[str] = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)


def fastapi_dependency(validator: JwtValidator) -> Callable[..., JwtClaims]:
    """Return a FastAPI dependency that validates and returns claims.

    Construct the dependency once at startup (typically right after
    `from_env()`) and pass it to `Depends(...)` on protected routes.

    The returned callable raises FastAPI `HTTPException(401)` on any
    failure, with `WWW-Authenticate: Bearer` set so well-behaved
    clients know to send a token.
    """
    # Import lazily so importing this module does not require FastAPI
    # for non-FastAPI consumers.
    from fastapi import Header, HTTPException, status

    def _unauthorized(detail: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    def _extract_bearer_token(authorization: str | None) -> str | None:
        if not authorization:
            return None
        match = _BEARER_RE.match(authorization.strip())
        if not match:
            return None
        return match.group(1).strip() or None

    def dependency(
        authorization: str | None = Header(default=None),
    ) -> JwtClaims:
        token = _extract_bearer_token(authorization)
        if token is None:
            raise _unauthorized("missing or malformed Authorization header")
        try:
            return validator.validate(token)
        except JwtInvalidError as exc:
            raise _unauthorized("invalid token") from exc

    return dependency
