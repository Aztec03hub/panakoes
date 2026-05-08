"""JWT authentication for the summarization service.

Validates HS256 bearer tokens issued by the auth service. The contract
mirrors `services/auth` (ADR-005): payload has `sub` (user UUID),
`iss`, `aud`, `iat`, `exp`, and `jti`. Coverage on this module is held
to 100% per ADR-018, since a missed branch here is a security gap.
"""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from panakoes_summarization.config import Settings

_bearer_scheme = HTTPBearer(auto_error=False)


def _credentials_exception(detail: str) -> HTTPException:
    """Build a 401 response with the WWW-Authenticate header."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_token(token: str, settings: Settings) -> dict[str, object]:
    """Verify the JWT signature, issuer, audience, and expiry.

    Returns the validated claims. Any signature, issuer, audience, or
    expiry violation raises a `401 Unauthorized` so the caller cannot
    distinguish failure modes (per OWASP guidance).
    """
    if not settings.jwt_secret:
        # Misconfiguration: refuse to authenticate anyone rather than
        # silently accepting unsigned tokens.
        raise _credentials_exception("authentication is not configured")
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise _credentials_exception("invalid or expired token") from exc
    if not isinstance(claims, dict):  # pragma: no cover  # PyJWT contract guarantees dict
        raise _credentials_exception("invalid token payload")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise _credentials_exception("token missing subject")
    return claims


def get_settings() -> Settings:
    """Return a fresh `Settings` instance for dependency injection.

    Wrapped as a function so tests can override it via FastAPI's
    `dependency_overrides` map without touching the module-level state.
    """
    return Settings()


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    """FastAPI dependency that returns the authenticated user's `sub`.

    Raises `401 Unauthorized` if the bearer token is missing, malformed,
    or fails any validation check. Returns the JWT `sub` claim, which
    downstream handlers use as the owner identity for resource scoping.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _credentials_exception("missing bearer token")
    claims = _decode_token(credentials.credentials, settings)
    return str(claims["sub"])
