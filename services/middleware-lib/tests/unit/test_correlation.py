"""Tests for `CorrelationIdMiddleware` and helpers."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from panakoes_middleware.correlation import (
    REQUEST_ID_HEADER,
    CorrelationIdMiddleware,
    RequestIdLogFilter,
    get_request_id,
)


def _build_app() -> FastAPI:
    """Return a tiny app whose handler echoes the resolved request id."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/echo")
    async def echo() -> dict[str, str]:
        return {"request_id": get_request_id()}

    return app


@pytest.mark.unit
def test_generates_request_id_when_header_absent() -> None:
    """Missing `X-Request-Id` triggers a fresh ULID and round-trips it."""
    client = TestClient(_build_app())
    response = client.get("/echo")
    assert response.status_code == 200
    minted = response.headers[REQUEST_ID_HEADER]
    assert len(minted) == 26
    assert response.json() == {"request_id": minted}


@pytest.mark.unit
def test_passes_through_supplied_request_id() -> None:
    """A client-supplied `X-Request-Id` is reused for the response."""
    client = TestClient(_build_app())
    response = client.get("/echo", headers={REQUEST_ID_HEADER: "req_inbound_123"})
    assert response.headers[REQUEST_ID_HEADER] == "req_inbound_123"
    assert response.json() == {"request_id": "req_inbound_123"}


@pytest.mark.unit
def test_blank_request_id_header_is_replaced() -> None:
    """Whitespace-only headers are treated as missing and replaced."""
    client = TestClient(_build_app())
    response = client.get("/echo", headers={REQUEST_ID_HEADER: "   "})
    minted = response.headers[REQUEST_ID_HEADER]
    assert minted.strip() != ""
    assert len(minted) == 26


@pytest.mark.unit
def test_request_id_resets_after_request() -> None:
    """`get_request_id()` returns empty string outside a request context."""
    client = TestClient(_build_app())
    client.get("/echo")
    assert get_request_id() == ""


@pytest.mark.unit
def test_request_state_is_populated() -> None:
    """The middleware sets `request.state.request_id` for handlers."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    captured: dict[str, str] = {}

    @app.get("/inspect")
    async def inspect(request: Request) -> dict[str, str]:
        captured["id"] = request.state.request_id
        return {"id": request.state.request_id}

    client = TestClient(app)
    response = client.get("/inspect", headers={REQUEST_ID_HEADER: "req_state_check"})
    assert response.status_code == 200, response.text
    assert captured["id"] == "req_state_check"


@pytest.mark.unit
def test_request_id_log_filter_outside_context_uses_dash() -> None:
    """Outside a request, the log filter populates `request_id='-'`."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    filt = RequestIdLogFilter()
    assert filt.filter(record) is True
    assert record.request_id == "-"  # type: ignore[attr-defined]
