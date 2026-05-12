"""Shared pytest fixtures for the panakoes-auth-client library.

The fixtures here cover the building blocks every test reaches for:
- a stable signing secret, issuer, and audience
- a constructed `JwtValidator`
- a helper that signs a payload at a controllable issued-at time

Tests scrub `JWT_*` env vars per-test so `from_env` cases start from
a known baseline regardless of the host shell.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from jose import jwt

from panakoes_auth_client import JwtValidator

SECRET = "test-secret-do-not-use-in-prod"
ISSUER = "https://auth.test.panakoes.com"
AUDIENCE = "panakoes-test-api"

JWT_ENV_KEYS = ("JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE", "JWT_PUBLIC_JWKS_URL")


@pytest.fixture(autouse=True)
def clean_jwt_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove `JWT_*` env vars before each test."""
    for key in JWT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def validator() -> JwtValidator:
    """A `JwtValidator` configured with the shared test material."""
    return JwtValidator(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)


@pytest.fixture
def make_token() -> Callable[..., str]:
    """Return a helper that signs a payload as an HS256 token.

    Defaults match a well-formed token issued by the Auth service:
    `iss`, `aud`, `iat`, and `exp` populated, plus a `sub`. Tests
    pass `overrides=` to flip individual claims, or `secret=` /
    `algorithm=` to forge mismatches.
    """

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
                if value is _DELETE:
                    payload.pop(key, None)
                else:
                    payload[key] = value
        return jwt.encode(payload, secret, algorithm=algorithm)

    return _make


class _DeleteSentinel:
    """Marker passed in `overrides` to remove a default claim."""


_DELETE = _DeleteSentinel()


@pytest.fixture
def delete_sentinel() -> _DeleteSentinel:
    """Expose the delete sentinel so tests can drop default claims."""
    return _DELETE
