"""API Gateway v2 WebSocket $connect Lambda authorizer.

Validates the caller's HS256 JWT and returns API Gateway's expected
authorizer response shape:

    {"isAuthorized": true, "context": {...}}      on success
    {"isAuthorized": false}                        on any failure

Identity sources (in priority order):

1. `Authorization: Bearer <jwt>` header.
2. `?token=<jwt>` query string parameter (the browser-mic path).

If `Authorization` is present we use it; we do NOT silently fall
back to the query param when the header is malformed, because that
would let an attacker bypass header-validation by appending a query
param. Strict precedence is the safer default.

All token failures collapse to `{"isAuthorized": false}`. The
underlying reason is logged via structlog at WARN with a `reason`
field; never echo it to the client (it leaks attack signal).
"""

from __future__ import annotations

import logging
from typing import Any

from jose import jwt as jose_jwt
from panakoes_auth_client import JwtConfigError, JwtInvalidError, JwtValidator, from_env

logger = logging.getLogger(__name__)
if not logger.handlers:  # pragma: no cover - module-import bootstrap
    logger.setLevel(logging.INFO)


# Module-level validator cache. Constructed lazily on first invocation
# (so a missing env var at import time does not crash the Lambda
# cold-start) and reused for the lifetime of the Lambda execution
# environment. Tests reset this in conftest.py.
_VALIDATOR: JwtValidator | None = None


def _get_validator() -> JwtValidator:
    global _VALIDATOR
    if _VALIDATOR is None:
        _VALIDATOR = from_env()
    return _VALIDATOR


def extract_token(event: dict[str, Any]) -> str | None:
    """Return the JWT carried by an API Gateway v2 authorizer event.

    Priority:
      1. `Authorization: Bearer <jwt>` header (case-insensitive
         header lookup).
      2. `queryStringParameters.token`.

    Returns `None` if neither is present OR if the Authorization
    header is present but does not begin with the literal `Bearer `.
    The "header present but bad" case deliberately does not fall
    back to the query param.
    """
    headers = event.get("headers") or {}
    # API Gateway preserves header case from the client, so look up
    # both common spellings without iterating the whole dict.
    auth_value = headers.get("Authorization") or headers.get("authorization")
    if isinstance(auth_value, str):
        prefix = "Bearer "
        if auth_value.startswith(prefix):
            return str(auth_value[len(prefix) :].strip())
        # Header present but malformed: hard reject (no query fallback).
        return None

    query = event.get("queryStringParameters") or {}
    token = query.get("token")
    if isinstance(token, str) and token:
        return token

    return None


def build_response(
    *,
    authorized: bool,
    context: dict[str, str],
) -> dict[str, Any]:
    """Build the API Gateway v2 authorizer response shape.

    On reject we omit `context` entirely; API Gateway only consults
    the context map on the allow path.
    """
    if not authorized:
        return {"isAuthorized": False}
    return {"isAuthorized": True, "context": context}


def _build_context(token: str, validated_sub: str, validated_role: str | None) -> dict[str, str]:
    """Compose the authorizer context map from validated claims.

    The validator's `JwtClaims` model drops unknown claims via
    `extra='ignore'`, but downstream services want `tenant_id` when
    the token carries it. We re-decode unverified to read that one
    extra claim (signature already verified upstream, so this is
    safe). Unknown claim absence yields a smaller context, not an
    error.
    """
    context: dict[str, str] = {"user_id": validated_sub}
    if validated_role:
        context["role"] = validated_role

    # The token's signature was verified by the validator already; we
    # only re-decode unverified to read tenant_id which the strict
    # claims model drops. We never trust unverified claims for
    # authorization decisions; this is metadata-passthrough only.
    try:
        unverified: dict[str, Any] = jose_jwt.get_unverified_claims(token)
    except Exception:
        return context

    tenant_id = unverified.get("tenant_id")
    if isinstance(tenant_id, str) and tenant_id:
        context["tenant_id"] = tenant_id

    return context


def lambda_handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint invoked by API Gateway v2 on $connect.

    Returns `{"isAuthorized": true, "context": {...}}` on success;
    `{"isAuthorized": false}` on every failure. Never raises.
    """
    token = extract_token(event)
    if token is None:
        logger.warning("ws-authorizer reject", extra={"reason": "missing-token"})
        return build_response(authorized=False, context={})

    try:
        validator = _get_validator()
    except JwtConfigError:
        logger.warning("ws-authorizer reject", extra={"reason": "config-error"})
        return build_response(authorized=False, context={})

    try:
        claims = validator.validate(token)
    except JwtInvalidError as exc:
        logger.warning("ws-authorizer reject", extra={"reason": str(exc)})
        return build_response(authorized=False, context={})

    context = _build_context(token=token, validated_sub=claims.sub, validated_role=claims.role)
    return build_response(authorized=True, context=context)
