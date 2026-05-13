"""Deterministic JWT helpers for Panakoes service tests.

The Panakoes auth service signs tokens with HS256 in local development
and slice-1 production (RS256 + JWKS is deferred to slice 2). Every
service that consumes JWTs needs to forge tokens in tests; before this
helper, each test file copy-pasted the same `jose.jwt.encode` boilerplate
with slightly different claim shapes. That drift caused real bugs (one
service expected `iss` of `https://auth.panakoes.test`, another expected
`panakoes-auth`).

This module is the single source of truth for test JWTs.

## Defaults

| Parameter | Default | Why |
|---|---|---|
| `secret` | `"test-secret"` | Documented test-only string. Never used in real code. |
| `issuer` | `"panakoes-auth"` | Matches the project's local-dev `iss` convention. |
| `audience` | `"panakoes-api"` | Matches the project's local-dev `aud` convention. |
| `expires_in_seconds` | `3600` | One-hour validity, well past any realistic test runtime. |
| `scopes` | `[]` | No scopes by default; tests opt-in to RBAC paths. |

## Why HS256, not RS256

Slice-1 of Panakoes uses HS256 with a shared secret stored in AWS Secrets
Manager. RS256 + JWKS requires a key-pair rotation flow that is not yet
built; until it is, HS256 is the contract every service tests against.
When slice-2 ships RS256, this module gains a sibling `make_rs256_token`
helper; the existing `make_test_token` stays untouched so all current
tests keep passing.

## No real secrets, ever

The default `secret="test-secret"` is a documented test-only string. It
is never read from production configuration; ruff's bandit rule `S105`
is suppressed only on this module via `pyproject.toml`. Service tests
that need to round-trip against a different secret (e.g. one matching
the service's `_clean_env` fixture) pass `secret=...` explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as _pyjwt

# Documented test-only default. Never used in real code paths.
# Suppressed for bandit S105 in pyproject.toml on this module only.
_DEFAULT_TEST_SECRET = "test-secret"
_DEFAULT_ISSUER = "panakoes-auth"
_DEFAULT_AUDIENCE = "panakoes-api"
_DEFAULT_EXPIRES_IN_SECONDS = 3600
_HS256 = "HS256"


def make_test_token(
    sub: str,
    *,
    secret: str = _DEFAULT_TEST_SECRET,
    issuer: str = _DEFAULT_ISSUER,
    audience: str = _DEFAULT_AUDIENCE,
    expires_in_seconds: int = _DEFAULT_EXPIRES_IN_SECONDS,
    scopes: list[str] | None = None,
) -> str:
    """Issue an HS256 JWT shaped like a Panakoes auth-service token.

    Standard claims (`iss`, `sub`, `aud`, `iat`, `exp`) are always set.
    `scopes` is included only when non-empty; consumers that do not use
    RBAC see a token with no `scopes` claim, which mirrors slice-1
    auth-service behaviour.

    Defaults match the project's local-dev convention; pass any keyword
    explicitly to override. The signing secret defaults to a documented
    test-only string and must never be reused in real code.

    Returns the encoded JWT string ready for an `Authorization: Bearer`
    header (use `bearer_header(token)` to build the header dict).
    """
    if expires_in_seconds <= 0:
        raise ValueError("expires_in_seconds must be positive; use make_expired_token instead")
    if not sub:
        raise ValueError("sub must be a non-empty subject identifier")
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(seconds=expires_in_seconds)
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if scopes:
        payload["scopes"] = list(scopes)
    return _pyjwt.encode(payload, secret, algorithm=_HS256)


def make_expired_token(
    sub: str,
    *,
    secret: str = _DEFAULT_TEST_SECRET,
    issuer: str = _DEFAULT_ISSUER,
    audience: str = _DEFAULT_AUDIENCE,
    expired_seconds_ago: int = 60,
    scopes: list[str] | None = None,
) -> str:
    """Issue an HS256 JWT whose `exp` claim is already in the past.

    Convenience for testing 401-on-expiry paths without the caller
    juggling timestamps. `expired_seconds_ago` controls how stale the
    token is; the default of 60 seconds clears any realistic clock-skew
    leeway built into JWT consumers.
    """
    if expired_seconds_ago <= 0:
        raise ValueError("expired_seconds_ago must be positive")
    if not sub:
        raise ValueError("sub must be a non-empty subject identifier")
    now = datetime.now(UTC)
    expires_at = now - timedelta(seconds=expired_seconds_ago)
    issued_at = expires_at - timedelta(seconds=_DEFAULT_EXPIRES_IN_SECONDS)
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": sub,
        "aud": audience,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if scopes:
        payload["scopes"] = list(scopes)
    return _pyjwt.encode(payload, secret, algorithm=_HS256)


def bearer_header(token: str) -> dict[str, str]:
    """Wrap `token` in an `Authorization: Bearer` header dict.

    Returns the exact shape httpx, requests, and FastAPI's `TestClient`
    expect. Doing this in one place keeps test files free of
    `f"Bearer {token}"` boilerplate (and the off-by-one whitespace bug
    that boilerplate occasionally introduces).
    """
    if not token:
        raise ValueError("token must be non-empty")
    return {"Authorization": f"Bearer {token}"}
