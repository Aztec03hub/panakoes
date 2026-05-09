"""Auth dependencies for admin-api routes.

Tier 3 lifecycle operations require both:
1. An admin-role JWT (the same gate cost-api uses).
2. A recent step-up MFA challenge captured in the `mfa_step_up_at`
   claim. The default freshness window is 5 minutes; configurable via
   `STEP_UP_MAX_AGE_SECONDS` env var.

The dependency chain is:

    require_admin_with_step_up
      -> claims (role == "admin", mfa_step_up_at within window)
        |
        v
      get_jwt_claims
        |
        v
      get_validator (lazy, env-driven, lru_cached)

Tests override `require_admin_with_step_up` (or the layer below it) so
they don't need a real signing secret. The split into discrete
dependencies makes it easy to unit-test the step-up gate in isolation
from the rest of the chain.
"""

from __future__ import annotations

import os
import re
import time
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

DEFAULT_STEP_UP_MAX_AGE_SECONDS = 300  # 5 minutes


def step_up_max_age_seconds() -> int:
    """Read the step-up freshness window from env, with a 300s default.

    Surfaced as a function so tests can monkeypatch the env var without
    re-importing the module. Negative or non-int values fall back to the
    documented default rather than raising at request time.
    """
    raw = os.environ.get("STEP_UP_MAX_AGE_SECONDS", "")
    if not raw:
        return DEFAULT_STEP_UP_MAX_AGE_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_STEP_UP_MAX_AGE_SECONDS
    if parsed <= 0:
        return DEFAULT_STEP_UP_MAX_AGE_SECONDS
    return parsed


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
    """Extract and validate the bearer token, returning typed claims."""
    token = _extract_bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("missing or malformed Authorization header")
    try:
        return validator.validate(token)
    except JwtInvalidError as exc:
        logger.info("admin_api_jwt_invalid", error=str(exc))
        raise _unauthorized("invalid token") from exc


def require_admin(claims: Annotated[JwtClaims, Depends(get_jwt_claims)]) -> JwtClaims:
    """Require `role == "admin"`. 403 otherwise.

    Used by the audit-log read view and other read-only Tier 3 surfaces
    that don't perform state changes; they require admin role but not
    step-up MFA.
    """
    if claims.role != "admin":
        logger.info("admin_api_forbidden_non_admin", subject=claims.sub, role=claims.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return claims


def require_admin_with_step_up(
    claims: Annotated[JwtClaims, Depends(require_admin)],
) -> JwtClaims:
    """Require admin role AND a step-up MFA timestamp within the freshness window.

    Mutating Tier 3 operations (drain, terminate, revoke, rotate) gate
    on this dependency. The window prevents a long-lived admin session
    from being trivially abused: the user has to re-authenticate with
    a second factor within the last 5 minutes before invoking any
    dangerous operation.
    """
    if claims.mfa_step_up_at is None:
        logger.info("admin_api_step_up_missing", subject=claims.sub)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="step-up MFA required",
        )
    age = int(time.time()) - claims.mfa_step_up_at
    max_age = step_up_max_age_seconds()
    if age > max_age:
        logger.info(
            "admin_api_step_up_stale",
            subject=claims.sub,
            age_seconds=age,
            max_age_seconds=max_age,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="step-up MFA required (claim too old)",
        )
    return claims
