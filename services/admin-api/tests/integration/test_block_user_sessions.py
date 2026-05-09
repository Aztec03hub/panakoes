"""Integration tests for `POST /api/v1/admin/users/{user_id}/block-sessions`.

Validates the safety substrate handles a third operation shape:
bulk update across multiple rows, GSI-driven enumeration, and
partial-failure semantics.
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
    """Moto-backed audit + sessions (with UserSessionsIndex GSI) + lifecycle-state."""
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
                {"AttributeName": "session_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "UserSessionsIndex",
                    "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
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

        # Seed three sessions for user_target: two active, one already errored.
        # Plus one session for a different user (must be skipped by the user_id
        # filter on the GSI).
        for sid, uid, status in [
            ("sess_1", "user_target", "active"),
            ("sess_2", "user_target", "starting"),
            ("sess_3", "user_target", "errored"),
            ("sess_other", "user_other", "active"),
        ]:
            sessions.put_item(
                Item={"session_id": sid, "user_id": uid, "status": status}
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
async def test_block_user_sessions_happy_path(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """All active+starting+paused sessions for the user are blocked; others untouched."""
    response = await client_admin.post(
        "/api/v1/admin/users/user_target/block-sessions",
        json=_request_body(
            "op-1", "BLOCK USER user_target", "compromised credentials"
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["affected_count"] == 2
    assert body["result"]["skipped_count"] == 1  # sess_3 was already errored
    assert set(body["result"]["blocked_session_ids"]) == {"sess_1", "sess_2"}
    assert body["result"]["noop"] is False

    sessions_table = aws_resources["sessions"]
    assert sessions_table.get_item(Key={"session_id": "sess_1"})["Item"]["status"] == "errored"
    assert sessions_table.get_item(Key={"session_id": "sess_2"})["Item"]["status"] == "errored"
    # sess_3 already errored, untouched (no termination_source).
    sess3 = sessions_table.get_item(Key={"session_id": "sess_3"})["Item"]
    assert sess3["status"] == "errored"
    assert "termination_source" not in sess3
    # The other user's session was not touched at all.
    other = sessions_table.get_item(Key={"session_id": "sess_other"})["Item"]
    assert other["status"] == "active"


@pytest.mark.integration
async def test_block_user_sessions_noop_when_nothing_active(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """A user with no active sessions yields succeeded + affected_count=0 noop."""
    response = await client_admin.post(
        "/api/v1/admin/users/user_with_no_sessions/block-sessions",
        json=_request_body("op-2", "BLOCK USER user_with_no_sessions", "drill"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["affected_count"] == 0
    assert body["result"]["noop"] is True


@pytest.mark.integration
async def test_block_user_sessions_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    """Confirmation that targets a different user returns 400."""
    response = await client_admin.post(
        "/api/v1/admin/users/user_target/block-sessions",
        json=_request_body("op-3", "BLOCK USER user_someone_else", "wrong target"),
    )
    assert response.status_code == 400
    assert "confirmation mismatch" in response.json()["detail"].lower()


@pytest.mark.integration
async def test_block_user_sessions_idempotent_replay(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """Replay returns the cached envelope; no new audit rows or session updates."""
    body = _request_body("op-4", "BLOCK USER user_target", "first try")
    first = await client_admin.post(
        "/api/v1/admin/users/user_target/block-sessions", json=body
    )
    assert first.status_code == 200
    first_audit_id = first.json()["audit_request_id"]

    # Replay.
    second = await client_admin.post(
        "/api/v1/admin/users/user_target/block-sessions",
        json=_request_body("op-4", "BLOCK USER user_target", "replay"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_audit_id
    assert second.json()["result"]["affected_count"] == 2

    # Audit table still has exactly the original two rows.
    audit_count = len(aws_resources["audit"].scan().get("Items", []))
    assert audit_count == 2
