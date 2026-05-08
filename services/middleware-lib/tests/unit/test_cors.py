"""Tests for `make_cors_middleware` and `from_env`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from panakoes_middleware.cors import DEFAULT_METHODS, from_env, make_cors_middleware


def _build_app(*, allowed_origins: list[str], methods: list[str] | None = None) -> FastAPI:
    """Return an app preconfigured with the given CORS allowlist."""
    middleware = make_cors_middleware(
        allowed_origins=allowed_origins,
        allowed_methods=methods,
    )
    app = FastAPI(middleware=[middleware])

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.mark.unit
def test_preflight_returns_cors_headers_for_allowed_origin() -> None:
    """Preflight request from an allowed origin gets the matching headers."""
    client = TestClient(_build_app(allowed_origins=["https://app.panakoes.com"]))
    response = client.options(
        "/ping",
        headers={
            "Origin": "https://app.panakoes.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Custom",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.panakoes.com"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.unit
def test_preflight_without_allowed_origin_is_rejected() -> None:
    """A disallowed origin does not receive the allow-origin header."""
    client = TestClient(_build_app(allowed_origins=["https://allowed.example.com"]))
    response = client.options(
        "/ping",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.unit
def test_default_methods_match_documented_set() -> None:
    """`DEFAULT_METHODS` is the project-wide expected list."""
    assert DEFAULT_METHODS == ["GET", "POST", "DELETE", "OPTIONS"]


@pytest.mark.unit
def test_from_env_parses_comma_separated_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comma-separated env values are split, trimmed, and applied."""
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        "https://a.example.com, https://b.example.com ,, ",
    )
    middleware = from_env()
    options = middleware.kwargs
    assert options["allow_origins"] == [
        "https://a.example.com",
        "https://b.example.com",
    ]


@pytest.mark.unit
def test_from_env_empty_value_yields_empty_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset or empty env var produces an empty allowlist."""
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    middleware = from_env()
    assert middleware.kwargs["allow_origins"] == []
