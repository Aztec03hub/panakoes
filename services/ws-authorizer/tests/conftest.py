"""Shared pytest fixtures for the WebSocket authorizer Lambda.

Tests construct API Gateway v2 authorizer events explicitly so the
exact shape API Gateway hands the Lambda is visible inline. The
`make_token` helper signs HS256 tokens at a controllable issued-at
time so expiration and clock-skew cases are deterministic.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from jose import jwt

SECRET = "test-secret-do-not-use-in-prod"
ISSUER = "https://auth.test.panakoes.com"
AUDIENCE = "panakoes-test-api"


@pytest.fixture(autouse=True)
def _jwt_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the JWT_* env vars to the shared test material."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("JWT_ISSUER", ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", AUDIENCE)
    yield


@pytest.fixture(autouse=True)
def _reset_validator_cache() -> Iterator[None]:
    """Clear the module-level validator cache before each test.

    The authorizer caches `JwtValidator` at module import to avoid
    per-invocation construction cost. Tests need a clean slate so an
    env-var override between tests is honored.
    """
    os.environ.setdefault("JWT_SECRET", SECRET)
    from panakoes_ws_authorizer import authorizer as _mod

    _mod._VALIDATOR = None
    yield
    _mod._VALIDATOR = None


@pytest.fixture
def make_token() -> Callable[..., str]:
    """Return a helper that signs a payload as an HS256 token."""

    def _make(
        *,
        overrides: dict[str, Any] | None = None,
        secret: str = SECRET,
        algorithm: str = "HS256",
        issuer: str = ISSUER,
        audience: str = AUDIENCE,
        now: int | None = None,
        ttl_seconds: int = 300,
    ) -> str:
        issued_at = now if now is not None else int(time.time())
        payload: dict[str, Any] = {
            "sub": "user_abc",
            "iss": issuer,
            "aud": audience,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
        }
        if overrides:
            for key, value in overrides.items():
                payload[key] = value
        return jwt.encode(payload, secret, algorithm=algorithm)

    return _make


@pytest.fixture
def make_event() -> Callable[..., dict[str, Any]]:
    """Return a helper that builds an API Gateway v2 authorizer event.

    The shape mirrors what API Gateway hands a REQUEST-type Lambda
    authorizer for a WebSocket $connect: headers + query-string
    parameters + a route arn under `methodArn`.
    """

    def _make(
        *,
        token: str | None = None,
        token_in: str = "query",
        method_arn: str = "arn:aws:execute-api:us-east-1:000000000000:abc/dev/$connect",
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        query: dict[str, str] = {}
        if token is not None:
            if token_in == "header":
                headers["Authorization"] = f"Bearer {token}"
            elif token_in == "header-bad":
                headers["Authorization"] = token  # no Bearer prefix
            elif token_in == "query":
                query["token"] = token
            else:
                raise ValueError(f"unknown token_in: {token_in}")
        return {
            "type": "REQUEST",
            "methodArn": method_arn,
            "headers": headers,
            "queryStringParameters": query,
            "requestContext": {
                "routeKey": "$connect",
                "connectionId": "abc-connection-id",
                "eventType": "CONNECT",
            },
        }

    return _make
