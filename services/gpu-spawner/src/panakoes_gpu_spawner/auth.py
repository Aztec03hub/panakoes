"""JWT verification dependency for the GPU Spawner.

Mirrors the Auth-service contract documented in
`services/ingestion-api/src/panakoes_ingestion_api/auth.py` with one
extra claim this service cares about: `actor_type`.

`actor_type` discriminates between user-actor tokens (issued to a real
human session) and service-actor tokens (issued to one Panakoes service
calling another). The GPU Spawner has compute-spawning authority, so
the dangerous routes (`POST /spawn`, `DELETE /spawn/{id}`) require an
`actor_type=service` token. Read-only state lookup permits either.

Every failure mode (missing header, malformed bearer, expired token,
bad signature, claim mismatch, missing actor_type) maps to HTTP 401
with a stable WWW-Authenticate header. Authorization failures (a valid
user-actor token reaching a service-only route) map to 403.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import ExpiredSignatureError, InvalidTokenError

from panakoes_gpu_spawner.config import Settings

_BEARER_RE: re.Pattern[str] = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)

ActorType = Literal["user", "service"]


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The authenticated principal extracted from a verified JWT.

    `actor_id` is the `sub` claim. For user actors it is the user id;
    for service actors it is the calling service's logical name (for
    example `session-manager`). `session_id` is the `jti` claim and
    represents the streaming session for user actors; for service
    actors it carries the session being acted on so audit events stay
    correlatable across the user-then-service hop.
    """

    actor_id: str
    actor_type: ActorType
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


def _forbidden(detail: str) -> HTTPException:
    """Build a 403 response. No WWW-Authenticate; the caller IS authenticated."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def verify_jwt(token: str, settings: Settings) -> AuthenticatedPrincipal:
    """Verify an HS256 JWT and return the authenticated principal.

    Raises `HTTPException(401)` for any verification failure. A valid
    token must carry `sub`, `jti`, and `actor_type` claims; the
    `actor_type` claim must be either `user` or `service`.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    except ExpiredSignatureError as exc:
        raise _unauthorized("token expired") from exc
    except InvalidTokenError as exc:
        raise _unauthorized("invalid token") from exc

    sub = payload.get("sub")
    jti = payload.get("jti")
    actor_type = payload.get("actor_type")
    if not isinstance(sub, str) or not sub:
        raise _unauthorized("invalid token claims")
    if not isinstance(jti, str) or not jti:
        raise _unauthorized("invalid token claims")
    if actor_type not in ("user", "service"):
        raise _unauthorized("invalid token claims")
    return AuthenticatedPrincipal(
        actor_id=sub,
        actor_type=actor_type,
        session_id=jti,
    )


def get_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(_settings)],
) -> AuthenticatedPrincipal:
    """FastAPI dependency that returns the authenticated principal or 401s."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("missing or malformed Authorization header")
    return verify_jwt(token, settings)


def require_service_actor(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> AuthenticatedPrincipal:
    """FastAPI dependency: 403 unless the principal is a service actor.

    Used to gate the dangerous routes. The Auth service's RBAC layer
    already determines who can mint a service-actor token, so once we
    see one we trust it for the route's purpose.
    """
    if principal.actor_type != "service":
        raise _forbidden("service-actor token required")
    return principal
