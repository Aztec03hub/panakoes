"""Tests for the `@audit_event` route-handler decorator."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from panakoes_audit import MemoryAuditStore

from panakoes_middleware.audit import audit_event
from panakoes_middleware.correlation import CorrelationIdMiddleware


@dataclass
class FakeUser:
    """Stand-in for the JWT-validated user object on `request.state.user`."""

    sub: str


def _build_app() -> FastAPI:
    """Return an app that puts `request.state.user` and exposes audit-decorated routes."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.middleware("http")
    async def attach_user(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.headers.get("x-test-user"):
            request.state.user = FakeUser(sub=request.headers["x-test-user"])
        return await call_next(request)

    @app.get("/widgets")
    @audit_event(action="widget.listed", source_service="widget-api")
    async def list_widgets(request: Request) -> dict[str, str]:
        return {"items": "0"}

    @app.post("/widgets/explode")
    @audit_event(action="widget.exploded", source_service="widget-api")
    async def explode(request: Request) -> dict[str, str]:
        raise HTTPException(status_code=500, detail="boom")

    return app


@pytest.mark.unit
def test_emits_event_on_success(memory_audit_store: MemoryAuditStore) -> None:
    """A successful handler produces one audit event with the resolved actor."""
    client = TestClient(_build_app())
    response = client.get("/widgets", headers={"X-Test-User": "user_42"})
    assert response.status_code == 200
    assert len(memory_audit_store.events) == 1
    event = memory_audit_store.events[0]
    assert event.actor_id == "user_42"
    assert event.actor_type == "user"
    assert event.action == "widget.listed"
    assert event.source_service == "widget-api"
    assert event.details["path"] == "/widgets"
    assert event.details["method"] == "GET"
    assert event.details["status"] == "success"
    assert event.details["status_code"] == 200


@pytest.mark.unit
def test_emits_event_on_failure(memory_audit_store: MemoryAuditStore) -> None:
    """A handler that raises still emits one event marked failed."""
    client = TestClient(_build_app())
    response = client.post(
        "/widgets/explode",
        headers={"X-Test-User": "user_42"},
    )
    assert response.status_code == 500
    assert len(memory_audit_store.events) == 1
    event = memory_audit_store.events[0]
    assert event.action == "widget.exploded"
    assert event.details["status"] == "failed"
    assert event.details["method"] == "POST"
    assert event.details["path"] == "/widgets/explode"


@pytest.mark.unit
def test_anonymous_actor_when_no_user(memory_audit_store: MemoryAuditStore) -> None:
    """A request without a JWT-resolved user records actor=anonymous."""
    client = TestClient(_build_app())
    response = client.get("/widgets")
    assert response.status_code == 200
    event = memory_audit_store.events[0]
    assert event.actor_id == "anonymous"
    assert event.actor_type == "anonymous"


@pytest.mark.unit
def test_request_id_propagates_into_audit_event(
    memory_audit_store: MemoryAuditStore,
) -> None:
    """The `CorrelationIdMiddleware` request id is recorded on the event."""
    client = TestClient(_build_app())
    response = client.get(
        "/widgets",
        headers={"X-Request-Id": "req_traced_1", "X-Test-User": "user_42"},
    )
    assert response.status_code == 200
    event = memory_audit_store.events[0]
    assert event.request_id == "req_traced_1"


@pytest.mark.unit
def test_decorator_rejects_sync_handlers() -> None:
    """`@audit_event` only wraps async handlers."""

    def sync_handler() -> None:
        return None

    with pytest.raises(TypeError, match="async"):
        audit_event(action="x.y", source_service="svc")(sync_handler)  # type: ignore[arg-type]


@pytest.mark.unit
def test_action_without_dot_falls_back_to_full_string(
    memory_audit_store: MemoryAuditStore,
) -> None:
    """If the action string has no dot, the resource_type matches the action."""
    # Use the audit-lib's regex constraints by using a valid action; the
    # decorator's `_domain_of` helper handles the no-dot case gracefully
    # for callers who reach for it via a direct path.
    from panakoes_middleware.audit import _domain_of, _status_code_of

    assert _domain_of("widget.created") == "widget"
    assert _domain_of("nodot") == "nodot"
    assert _status_code_of(None) == 200

    class Has:
        status_code = 201

    assert _status_code_of(Has()) == 201


@pytest.mark.unit
def test_user_without_sub_records_anonymous(memory_audit_store: MemoryAuditStore) -> None:
    """A `state.user` without a `sub` attribute is treated as anonymous."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.middleware("http")
    async def attach_user(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.user = object()  # has no .sub
        return await call_next(request)

    @app.get("/x")
    @audit_event(action="thing.read", source_service="svc")
    async def x(request: Request) -> dict[str, str]:
        return {"ok": "true"}

    client = TestClient(app)
    response = client.get("/x")
    assert response.status_code == 200
    event = memory_audit_store.events[0]
    assert event.actor_id == "anonymous"


@pytest.mark.unit
async def test_decorator_emits_when_no_request_in_signature(
    memory_audit_store: MemoryAuditStore,
) -> None:
    """A handler without a `Request` parameter still records an audit row."""

    @audit_event(action="thing.read", source_service="svc")
    async def handler() -> dict[str, str]:
        return {"ok": "true"}

    result = await handler()
    assert result == {"ok": "true"}
    assert len(memory_audit_store.events) == 1
    event = memory_audit_store.events[0]
    assert event.actor_id == "anonymous"
    assert event.actor_type == "anonymous"


@pytest.mark.unit
def test_audit_event_finds_request_in_kwargs(memory_audit_store: MemoryAuditStore) -> None:
    """The decorator works whether `Request` is passed positional or keyword."""
    from panakoes_middleware.audit import _find_request

    scope = {
        "type": "http",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "method": "GET",
        "path": "/",
        "query_string": b"",
    }
    req = Request(scope)
    assert _find_request((), {"request": req}) is req
    assert _find_request((req,), {}) is req
    assert _find_request((1, "two"), {"x": "y"}) is None
