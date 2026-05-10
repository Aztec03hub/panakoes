"""Integration tests for `POST /api/v1/admin/streaming-sessions/{id}/kill`."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import boto3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from panakoes_auth_client import JwtClaims

from panakoes_admin_api.auth import require_admin_with_step_up
from panakoes_admin_api.dependencies import (
    get_audit_table,
    get_eventbridge_publisher,
    get_lifecycle_state,
    get_streaming_sessions_table,
)
from panakoes_admin_api.eventbridge import EventBridgePublisher
from panakoes_admin_api.lifecycle_state import LifecycleStateStore
from panakoes_admin_api.main import app


def _admin_with_step_up() -> JwtClaims:
    return JwtClaims(
        sub="user_admin",
        iss="https://auth.example",
        aud="admin-api",
        iat=int(time.time()) - 60,
        exp=int(time.time()) + 3600,
        role="admin",
        mfa_step_up_at=int(time.time()) - 30,
    )


class FakeEventBridgePublisher:
    """In-memory publisher that records calls and returns synthetic event ids."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next_id = 0

    def put_event(
        self,
        *,
        source: str,
        detail_type: str,
        detail: dict[str, Any],
    ) -> str:
        self._next_id += 1
        event_id = f"evt-{self._next_id:04d}"
        self.calls.append(
            {
                "source": source,
                "detail_type": detail_type,
                "detail": detail,
                "event_id": event_id,
            }
        )
        return event_id


@pytest.fixture
def publisher() -> FakeEventBridgePublisher:
    return FakeEventBridgePublisher()


@pytest.fixture
def aws_resources() -> Iterator[dict[str, Any]]:
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        audit = ddb.create_table(
            TableName="panakoes-test-audit-log",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sessions = ddb.create_table(
            TableName="panakoes-test-streaming-sessions",
            KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "session_id", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        lifecycle = ddb.create_table(
            TableName="panakoes-test-lifecycle-state",
            KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "idempotency_key", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for t in (audit, sessions, lifecycle):
            t.wait_until_exists()

        sessions.put_item(
            Item={
                "session_id": "sess_active",
                "user_id": "u1",
                "status": "active",
            }
        )
        sessions.put_item(
            Item={
                "session_id": "sess_done",
                "user_id": "u2",
                "status": "errored",
            }
        )

        yield {
            "audit": audit,
            "sessions": sessions,
            "lifecycle_state": LifecycleStateStore(table=lifecycle),
        }


@pytest_asyncio.fixture
async def client_admin(
    aws_resources: dict[str, Any],
    publisher: FakeEventBridgePublisher,
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_streaming_sessions_table] = lambda: aws_resources[
        "sessions"
    ]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    pub: EventBridgePublisher = publisher
    app.dependency_overrides[get_eventbridge_publisher] = lambda: pub
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


def _body(idempotency_key: str, confirmation: str, reason: str) -> dict[str, Any]:
    return {
        "idempotency_key": idempotency_key,
        "confirmation": confirmation,
        "params": {"reason": reason},
    }


@pytest.mark.integration
async def test_kill_streaming_session_happy_path(
    client_admin: AsyncClient,
    aws_resources: dict[str, Any],
    publisher: FakeEventBridgePublisher,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_active/kill",
        json=_body("op-1", "KILL STREAM sess_active", "abuse"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["session_id"] == "sess_active"
    assert body["result"]["was_active"] is True
    assert body["result"]["eventbridge_event_id"] == "evt-0001"

    # Session row updated.
    item = aws_resources["sessions"].get_item(Key={"session_id": "sess_active"})[
        "Item"
    ]
    assert item["status"] == "errored"
    assert item["termination_source"] == "tier3.kill-streaming-session"

    # Tombstone event was published.
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["detail_type"] == "StreamingSessionKilled"
    assert publisher.calls[0]["detail"]["session_id"] == "sess_active"


@pytest.mark.integration
async def test_kill_streaming_session_already_done_was_active_false(
    client_admin: AsyncClient,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_done/kill",
        json=_body("op-2", "KILL STREAM sess_done", "redundant"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["was_active"] is False


@pytest.mark.integration
async def test_kill_streaming_session_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_active/kill",
        json=_body("op-3", "KILL STREAM nope", "x"),
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_kill_streaming_session_unknown_returns_failed_envelope(
    client_admin: AsyncClient, publisher: FakeEventBridgePublisher
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_missing/kill",
        json=_body("op-4", "KILL STREAM sess_missing", "x"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "not found" in body["error_message"].lower()
    # No event published if the row didn't exist.
    assert publisher.calls == []


@pytest.mark.integration
async def test_kill_streaming_session_idempotent_replay(
    client_admin: AsyncClient,
    aws_resources: dict[str, Any],
    publisher: FakeEventBridgePublisher,
) -> None:
    body = _body("op-5", "KILL STREAM sess_active", "first")
    first = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_active/kill", json=body
    )
    assert first.status_code == 200
    first_audit_id = first.json()["audit_request_id"]

    second = await client_admin.post(
        "/api/v1/admin/streaming-sessions/sess_active/kill",
        json=_body("op-5", "KILL STREAM sess_active", "second"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_audit_id
    # Only one EventBridge publish despite two requests.
    assert len(publisher.calls) == 1


@pytest.mark.integration
async def test_kill_streaming_session_unauthenticated_returns_401(
    aws_resources: dict[str, Any], publisher: FakeEventBridgePublisher
) -> None:
    app.dependency_overrides.clear()
    pub: EventBridgePublisher = publisher
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_streaming_sessions_table] = lambda: aws_resources[
        "sessions"
    ]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    app.dependency_overrides[get_eventbridge_publisher] = lambda: pub
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/api/v1/admin/streaming-sessions/sess_active/kill",
                json=_body("op-6", "KILL STREAM sess_active", "x"),
            )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
