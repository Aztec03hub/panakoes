"""JWT verification dependency for FastAPI.

Mirrors the contract the Auth service publishes (see
`services/auth/src/auth/jwt.ts`):

- HS256 with shared secret in `AUTH_JWT_SECRET`.
- Issuer and audience claim-validated on every verification.
- Payload shape: `{ sub: user_id, email, iat, exp, jti: session_id }`.

Every failure mode (missing header, malformed bearer, expired token,
bad signature, claim mismatch) maps to HTTP 401 with a stable
WWW-Authenticate header. We intentionally do NOT echo the underlying
library exception to clients.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError, jwt

from panakoes_ingestion_api.config import Settings

_BEARER_RE: re.Pattern[str] = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AuthenticatedUser:
    """The authenticated principal extracted from a verified JWT."""

    user_id: str
    email: str
    session_id: str


@lru_cache(maxsize=1)
def _settings() -> Settings:
    """Cache the `Settings` instance so each request does not re-read env."""
    return Settings()


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Pull the bearer token from an `Authorization` header.

    Returns `None` if the header is missing, empty, or not a Bearer
    scheme. Centralised so handlers and tests reach for the same parser.
    """
    if not authorization:
        return None
    match = _BEARER_RE.match(authorization.strip())
    if not match:
        return None
    return match.group(1).strip() or None


def _unauthorized(detail: str) -> HTTPException:
    """Build a 401 response with the standard WWW-Authenticate header."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_jwt(token: str, settings: Settings) -> AuthenticatedUser:
    """Verify an HS256 JWT and return the authenticated principal.

    Raises `HTTPException(401)` for any verification failure. Successful
    verification requires the configured issuer and audience claims plus
    the Auth service's documented payload shape.
    """
    try:
        payload = jwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=["HS256"],
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
        )
    except ExpiredSignatureError as exc:
        raise _unauthorized("token expired") from exc
    except JWTError as exc:
        raise _unauthorized("invalid token") from exc

    sub = payload.get("sub")
    email = payload.get("email")
    jti = payload.get("jti")
    if not isinstance(sub, str) or not sub:
        raise _unauthorized("invalid token claims")
    if not isinstance(email, str) or not email:
        raise _unauthorized("invalid token claims")
    if not isinstance(jti, str) or not jti:
        raise _unauthorized("invalid token claims")
    return AuthenticatedUser(user_id=sub, email=email, session_id=jti)


def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(_settings)],
) -> AuthenticatedUser:
    """FastAPI dependency that returns the authenticated user or 401s."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("missing or malformed Authorization header")
    return verify_jwt(token, settings)
