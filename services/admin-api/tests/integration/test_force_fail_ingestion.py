"""Integration tests for `POST /api/v1/admin/ingestions/{id}/force-fail`.

Validates the safety substrate generalizes to a second operation
that touches a different table (`panakoes-dev-ingestion`) and uses
a GSI lookup (`IngestionIdIndex`) instead of a primary-key get.
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
    get_ingestion_table,
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
    """Moto-backed audit + ingestion + lifecycle-state tables.

    The ingestion table is provisioned with the same shape as
    `infra/dev/data/`: composite USER#/INGESTION# primary key plus
    the IngestionIdIndex GSI keyed on `ingestion_id`. The handler
    queries the GSI to resolve a bare ingestion_id to its primary
    key before issuing the UpdateItem.
    """
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
        ingestion = ddb.create_table(
            TableName="panakoes-test-ingestion",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "ingestion_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "IngestionIdIndex",
                    "KeySchema": [
                        {"AttributeName": "ingestion_id", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
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
        for t in (audit, ingestion, sessions, lifecycle):
            t.wait_until_exists()

        # Seed a known-good ingestion record.
        ingestion.put_item(
            Item={
                "pk": "USER#user_xyz",
                "sk": "INGESTION#ing_abc",
                "ingestion_id": "ing_abc",
                "user_id": "user_xyz",
                "status": "pending",
                "created_at": "2026-05-09T00:00:00Z",
            }
        )

        yield {
            "audit": audit,
            "ingestion": ingestion,
            "sessions": sessions,
            "lifecycle_state": LifecycleStateStore(table=lifecycle),
        }


@pytest_asyncio.fixture
async def client_admin(
    aws_resources: dict[str, Any],
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_ingestion_table] = lambda: aws_resources["ingestion"]
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
async def test_force_fail_ingestion_happy_path(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """Admin + step-up + correct confirmation -> 200 with succeeded envelope."""
    response = await client_admin.post(
        "/api/v1/admin/ingestions/ing_abc/force-fail",
        json=_request_body(
            "op-1", "FAIL ing_abc", "stuck upload from abusive tenant"
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["ingestion_id"] == "ing_abc"
    assert body["result"]["status"] == "failed"

    # Ingestion row was updated.
    item = aws_resources["ingestion"].get_item(
        Key={"pk": "USER#user_xyz", "sk": "INGESTION#ing_abc"}
    )["Item"]
    assert item["status"] == "failed"
    assert item["failure_reason"] == "stuck upload from abusive tenant"
    assert item["failure_source"] == "tier3.force-fail-ingestion"

    # Audit log has both intent + outcome rows tagged with tier3_action.
    audit_items = aws_resources["audit"].scan().get("Items", [])
    actions = sorted([i["action"] for i in audit_items])
    assert actions == [
        "tier3.force-fail-ingestion.intent",
        "tier3.force-fail-ingestion.outcome",
    ]


@pytest.mark.integration
async def test_force_fail_ingestion_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    """Confirmation that targets a different ingestion returns 400."""
    response = await client_admin.post(
        "/api/v1/admin/ingestions/ing_abc/force-fail",
        json=_request_body("op-2", "FAIL ing_xyz", "wrong target"),
    )
    assert response.status_code == 400
    assert "confirmation mismatch" in response.json()["detail"].lower()


@pytest.mark.integration
async def test_force_fail_ingestion_unknown_id_returns_failed_envelope(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """A non-existent ingestion_id surfaces as a 200-with-failed envelope."""
    response = await client_admin.post(
        "/api/v1/admin/ingestions/ing_does_not_exist/force-fail",
        json=_request_body(
            "op-3",
            "FAIL ing_does_not_exist",
            "trying to fail a missing ingestion",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "not found" in body["error_message"].lower()

    # Both audit rows present, outcome=failure.
    audit_items = aws_resources["audit"].scan().get("Items", [])
    outcome_rows = [
        i for i in audit_items if i["action"].endswith(".outcome")
    ]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"] == "failure"


@pytest.mark.integration
async def test_force_fail_ingestion_idempotent_replay(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    """Replay with the same idempotency_key returns the cached envelope."""
    body = _request_body("op-4", "FAIL ing_abc", "first try")
    first = await client_admin.post(
        "/api/v1/admin/ingestions/ing_abc/force-fail", json=body
    )
    assert first.status_code == 200
    first_audit_id = first.json()["audit_request_id"]

    second = await client_admin.post(
        "/api/v1/admin/ingestions/ing_abc/force-fail",
        json=_request_body("op-4", "FAIL ing_abc", "second try"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_audit_id

    # Audit log still has exactly the original two rows.
    audit_count = len(aws_resources["audit"].scan().get("Items", []))
    assert audit_count == 2
