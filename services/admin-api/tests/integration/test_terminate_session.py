"""Integration tests for `POST /api/v1/admin/sessions/{id}/terminate`.

These tests exercise the full Tier 3 safety stack against moto-backed
AWS resources:

- Step-up MFA gate (`require_admin_with_step_up`).
- Typed-confirmation match (`f"TERMINATE {session_id}"`).
- Idempotency-by-key replay (`LifecycleStateStore`).
- Audit-before-AND-after (`audit_lifecycle`).
- Operation handler against the streaming-sessions table.

Tests override `require_admin_with_step_up` and the table dependencies
to inject test fixtures without spinning up real AWS or signing real
JWTs.
"""

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
    get_lifecycle_state,
    get_streaming_sessions_table,
)
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


@pytest.fixture
def aws_resources() -> Iterator[dict[str, Any]]:
    """Moto-backed audit-log + streaming-sessions + lifecycle-state tables."""
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
        audit.wait_until_exists()
        sessions.wait_until_exists()
        lifecycle.wait_until_exists()

        # Seed a known-good session.
        sessions.put_item(
            Item={
                "session_id": "sess_123",
                "user_id": "user_xyz",
                "status": "active",
                "created_at": "2026-05-09T00:00:00Z",
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
) -> AsyncIterator[AsyncClient]:
    """Async client with admin+step-up auth and moto-backed tables."""
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_streaming_sessions_table] = lambda: aws_resources[
        "sessions"
    ]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.clear()


def _request_body(idempotency_key: str, confirmation: str, reason: str) -> dict[str, Any]:
    return {
        "idempotency_key": idempotency_key,
        "confirmation": confirmation,
        "params": {"reason": reason},
    }


@pytest.mark.integration
async def test_terminate_session_happy_path(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """Admin + step-up + correct confirmation -> 200 with succeeded envelope."""
    response = await client_admin.post(
        "/api/v1/admin/sessions/sess_123/terminate",
        json=_request_body("op-1", "TERMINATE sess_123", "ops drill"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["idempotency_key"] == "op-1"
    assert body["audit_request_id"]
    assert body["result"]["session_id"] == "sess_123"

    # Session row was updated.
    item = aws_resources["sessions"].get_item(Key={"session_id": "sess_123"})["Item"]
    assert item["status"] == "errored"
    assert item["termination_reason"] == "ops drill"

    # Audit log has both intent and outcome rows.
    audit_items = aws_resources["audit"].scan().get("Items", [])
    actions = sorted([i["action"] for i in audit_items])
    assert actions == ["tier3.terminate-session.intent", "tier3.terminate-session.outcome"]
    outcomes = [i["outcome"] for i in audit_items if i["action"].endswith(".outcome")]
    assert outcomes == ["success"]


@pytest.mark.integration
async def test_terminate_session_idempotent_replay(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """Re-submitting the same idempotency_key returns the cached envelope, no extra side-effects."""
    body = _request_body("op-2", "TERMINATE sess_123", "first try")
    first = await client_admin.post(
        "/api/v1/admin/sessions/sess_123/terminate", json=body
    )
    assert first.status_code == 200
    first_envelope = first.json()

    # Replay with same idempotency_key.
    second = await client_admin.post(
        "/api/v1/admin/sessions/sess_123/terminate",
        json=_request_body("op-2", "TERMINATE sess_123", "second try"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_envelope["audit_request_id"]
    assert second.json()["status"] == "succeeded"

    # Audit table still has exactly the original two rows (no new writes).
    audit_count = len(aws_resources["audit"].scan().get("Items", []))
    assert audit_count == 2


@pytest.mark.integration
async def test_terminate_session_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    """A confirmation string that doesn't match the template returns 400."""
    response = await client_admin.post(
        "/api/v1/admin/sessions/sess_123/terminate",
        json=_request_body("op-3", "DRAIN something-else", "wrong target"),
    )
    assert response.status_code == 400
    assert "confirmation mismatch" in response.json()["detail"].lower()


@pytest.mark.integration
async def test_terminate_session_handler_failure_returns_failed_envelope(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """A non-existent session causes the handler to raise; envelope status=failed."""
    response = await client_admin.post(
        "/api/v1/admin/sessions/sess_does_not_exist/terminate",
        json=_request_body(
            "op-4",
            "TERMINATE sess_does_not_exist",
            "trying to terminate a missing session",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "not found" in body["error_message"].lower()

    # Audit log has both rows; outcome is failure.
    audit_items = aws_resources["audit"].scan().get("Items", [])
    outcome_rows = [
        i for i in audit_items if i["action"].endswith(".outcome")
    ]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"] == "failure"


@pytest.mark.integration
async def test_terminate_session_step_up_gate_required(
    aws_resources: dict[str, Any],
) -> None:
    """Without step-up override, the request is rejected by the gate.

    The auth dependency is NOT overridden here; the actual validator
    runs against the seeded JWT env vars but receives no token, so the
    `get_jwt_claims` upstream raises 401 before we reach the step-up
    check. This is the strongest signal that the gate is wired.
    """
    app.dependency_overrides.clear()
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_streaming_sessions_table] = lambda: aws_resources[
        "sessions"
    ]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/api/v1/admin/sessions/sess_123/terminate",
                json=_request_body("op-5", "TERMINATE sess_123", "no auth at all"),
            )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
