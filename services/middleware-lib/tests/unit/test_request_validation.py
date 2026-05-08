"""Tests for `MaxRequestSizeMiddleware` and `RequiredHeadersMiddleware`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from panakoes_middleware.request_validation import (
    DEFAULT_MAX_BYTES,
    MaxRequestSizeMiddleware,
    RequiredHeadersMiddleware,
)


def _size_app(max_bytes: int) -> FastAPI:
    """Return an app guarded by `MaxRequestSizeMiddleware`."""
    app = FastAPI()
    app.add_middleware(MaxRequestSizeMiddleware, max_bytes=max_bytes)

    @app.post("/upload")
    async def upload(payload: dict[str, str]) -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.mark.unit
def test_default_max_bytes_value() -> None:
    """The 10 MiB default matches the documented contract."""
    assert DEFAULT_MAX_BYTES == 10 * 1024 * 1024


@pytest.mark.unit
def test_oversized_request_returns_413() -> None:
    """A `Content-Length` over `max_bytes` is rejected before the body reads."""
    client = TestClient(_size_app(max_bytes=100))
    response = client.post(
        "/upload",
        content=b"x" * 200,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


@pytest.mark.unit
def test_within_limit_passes_through() -> None:
    """A request inside the budget reaches the handler normally."""
    client = TestClient(_size_app(max_bytes=10_000))
    response = client.post("/upload", json={"hello": "world"})
    assert response.status_code == 200


@pytest.mark.unit
def test_invalid_content_length_is_rejected_as_400() -> None:
    """A non-integer `Content-Length` produces a 400 (not a server error)."""
    middleware_app = _size_app(max_bytes=1_000)

    client = TestClient(middleware_app)
    # httpx will not let us inject a malformed numeric content-length easily,
    # so we test the middleware directly via a manual ASGI scope.
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    async def echo(request: object) -> PlainTextResponse:  # pragma: no cover - placeholder
        return PlainTextResponse("ok")

    base = Starlette(routes=[Route("/upload", echo, methods=["POST"])])
    guarded = MaxRequestSizeMiddleware(base, max_bytes=1_000)
    client = TestClient(guarded)
    response = client.post(
        "/upload",
        content=b"hi",
        headers={"Content-Length": "not-a-number"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Content-Length header"}


@pytest.mark.unit
def test_chunked_request_without_content_length_is_allowed() -> None:
    """Without a `Content-Length` header, the middleware falls back to passthrough."""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient as RawTestClient

    async def echo(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    base = Starlette(routes=[Route("/upload", echo, methods=["POST"])])
    guarded = MaxRequestSizeMiddleware(base, max_bytes=10)

    client = RawTestClient(guarded)

    # Send via a generator body so httpx omits Content-Length.
    from collections.abc import Iterator

    def body_iter() -> Iterator[bytes]:
        yield b"hello"

    response = client.post("/upload", content=body_iter())
    # No content-length header was attached; middleware must pass through.
    assert response.status_code == 200


@pytest.mark.unit
def test_max_bytes_property() -> None:
    """The configured ceiling is reachable via `.max_bytes`."""
    from starlette.applications import Starlette

    base = Starlette()
    mw = MaxRequestSizeMiddleware(base, max_bytes=42)
    assert mw.max_bytes == 42


def _required_app(required: list[str]) -> FastAPI:
    """Return an app guarded by `RequiredHeadersMiddleware`."""
    app = FastAPI()
    app.add_middleware(RequiredHeadersMiddleware, required=required)

    @app.get("/secure")
    async def secure() -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.mark.unit
def test_required_headers_pass_through_when_present() -> None:
    """All required headers present means the handler runs."""
    client = TestClient(_required_app(["X-Tenant-Id", "X-Api-Key"]))
    response = client.get(
        "/secure",
        headers={"X-Tenant-Id": "t1", "X-Api-Key": "k1"},
    )
    assert response.status_code == 200


@pytest.mark.unit
def test_required_headers_400_when_missing() -> None:
    """Any missing header surfaces as a 400 with a list of names."""
    client = TestClient(_required_app(["X-Tenant-Id", "X-Api-Key"]))
    response = client.get("/secure", headers={"X-Tenant-Id": "t1"})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Missing required headers"
    assert body["missing"] == ["x-api-key"]


@pytest.mark.unit
def test_required_headers_match_is_case_insensitive() -> None:
    """RFC 7230: header names are case-insensitive."""
    client = TestClient(_required_app(["X-Tenant-Id"]))
    response = client.get("/secure", headers={"x-TENANT-id": "abc"})
    assert response.status_code == 200


@pytest.mark.unit
def test_required_property_returns_lowercased_names() -> None:
    """The `required` property surfaces the canonical lowercase form."""
    from starlette.applications import Starlette

    base = Starlette()
    mw = RequiredHeadersMiddleware(base, required=["X-A", "X-B"])
    assert mw.required == ["x-a", "x-b"]
