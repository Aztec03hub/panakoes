"""Tests for the FastAPI dependency factory.

These tests exercise the dependency end-to-end via a small FastAPI
app and `httpx.ASGITransport`, so we confirm header parsing,
validator wiring, and the `WWW-Authenticate` challenge work the way
real consumers will see them.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from panakoes_auth_client import JwtClaims, JwtValidator, fastapi_dependency


def _build_app(validator: JwtValidator) -> FastAPI:
    """Spin up a tiny FastAPI app that mirrors how services use the dep."""
    app = FastAPI()
    get_claims = fastapi_dependency(validator)
    claims_dep = Depends(get_claims)

    @app.get("/me")
    def me(claims: JwtClaims = claims_dep) -> dict[str, str]:
        return {"sub": claims.sub}

    return app


@pytest.mark.integration
async def test_valid_bearer_token_returns_200(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A well-formed `Authorization: Bearer <token>` returns the protected payload."""
    token = make_token(overrides={"sub": "user_xyz"})
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"sub": "user_xyz"}


@pytest.mark.integration
async def test_lowercase_bearer_scheme_is_accepted(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """The Bearer scheme match is case-insensitive (RFC 6750 says SHOULD treat as such)."""
    token = make_token()
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": f"bearer {token}"})

    assert resp.status_code == 200


@pytest.mark.integration
async def test_missing_authorization_header_returns_401(validator: JwtValidator) -> None:
    """No header at all returns 401 with the standard challenge."""
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me")

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
async def test_empty_authorization_header_returns_401(validator: JwtValidator) -> None:
    """Header present but empty is treated as missing."""
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": ""})

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
async def test_non_bearer_scheme_returns_401(validator: JwtValidator) -> None:
    """`Basic` or other schemes do not satisfy a Bearer requirement."""
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": "Basic abc123"})

    assert resp.status_code == 401


@pytest.mark.integration
async def test_bearer_with_empty_token_returns_401(validator: JwtValidator) -> None:
    """`Bearer` with whitespace and nothing else is rejected as malformed."""
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": "Bearer    "})

    assert resp.status_code == 401


@pytest.mark.integration
async def test_invalid_token_returns_401(validator: JwtValidator) -> None:
    """A garbage token gets the same 401 + Bearer challenge."""
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


@pytest.mark.integration
async def test_wrong_audience_token_returns_401(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A correctly-signed but wrong-audience token still 401s."""
    token = make_token(audience="someone-else")
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401


@pytest.mark.integration
async def test_authorization_header_with_extra_whitespace_works(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """Leading/trailing whitespace in the header does not defeat the parser."""
    token = make_token()
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": f"  Bearer  {token}  "})

    assert resp.status_code == 200


@pytest.mark.integration
async def test_unauthorized_response_does_not_leak_underlying_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """Failure detail is generic; clients learn nothing about why."""
    token = make_token(secret="forged")
    app = _build_app(validator)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    body = resp.json()
    assert "signature" not in body.get("detail", "").lower()
    assert "secret" not in body.get("detail", "").lower()
