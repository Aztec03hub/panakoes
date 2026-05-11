"""Auth dependencies for health-aggregator routes.

Mirrors `services/cost-api/src/panakoes_cost_api/auth.py` exactly.
Every `/v1/health-aggregator/*` route requires an admin-role JWT for
v0.1; this is Tier 1's only gated endpoint and the only thing
standing between the bundled mock and real production data.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from panakoes_auth_client import (
    JwtClaims,
    JwtInvalidError,
    JwtValidator,
    from_env,
)

logger = structlog.get_logger(__name__)

_BEARER_RE: re.Pattern[str] = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)


@lru_cache(maxsize=1)
def get_validator() -> JwtValidator:
    """Construct (or reuse) the JWT validator from env vars."""
    return from_env()


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


def get_jwt_claims(
    request: Request,
    validator: Annotated[JwtValidator, Depends(get_validator)],
) -> JwtClaims:
    """Extract and validate the bearer token; raise 401 otherwise."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("missing or malformed Authorization header")
    try:
        return validator.validate(token)
    except JwtInvalidError as exc:
        logger.info("health_aggregator_jwt_invalid", error=str(exc))
        raise _unauthorized("invalid token") from exc


def require_admin(claims: Annotated[JwtClaims, Depends(get_jwt_claims)]) -> JwtClaims:
    """Require role=admin; 403 otherwise."""
    if claims.role != "admin":
        logger.info(
            "health_aggregator_forbidden_non_admin",
            subject=claims.sub,
            role=claims.role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return claims
