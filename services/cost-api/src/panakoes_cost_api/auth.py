"""Auth dependencies for cost-api routes.

Cost endpoints require an admin-role JWT. The dependency chain is:

    require_admin   -> claims (role == "admin", else 403)
       |
       v
    get_jwt_claims  -> validated JwtClaims (or 401)
       |
       v
    get_validator   -> JwtValidator (lazy, env-driven, lru_cached)

Tests override `require_admin` (or `get_jwt_claims`) via
`app.dependency_overrides[...]` so they never need a real JWT secret
or the env-var dance.

The dependency does NOT use `panakoes_auth_client.fastapi_dependency`
because that helper bakes the validator in at construction time, which
makes the validator hard to swap in tests. Splitting `get_validator` and
`get_jwt_claims` here keeps the test override surface clean.
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
    """Construct (or reuse) the JWT validator from env vars.

    Reads `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`. Raises
    `JwtConfigError` if any are missing; FastAPI surfaces that as a 500
    so the operator sees the misconfiguration rather than a silent
    request that succeeds without auth.
    """
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
    """Extract and validate the bearer token, returning typed claims.

    Raises 401 with `WWW-Authenticate: Bearer` for any failure. The
    underlying error is logged but never echoed to the client.
    """
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("missing or malformed Authorization header")
    try:
        return validator.validate(token)
    except JwtInvalidError as exc:
        logger.info("cost_api_jwt_invalid", error=str(exc))
        raise _unauthorized("invalid token") from exc


def require_admin(claims: Annotated[JwtClaims, Depends(get_jwt_claims)]) -> JwtClaims:
    """Require the caller's JWT to carry `role: "admin"`. 403 otherwise."""
    if claims.role != "admin":
        logger.info("cost_api_forbidden_non_admin", subject=claims.sub, role=claims.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return claims
