"""Integration tests for `POST /api/v1/admin/api-keys/{api_key_id}/revoke`."""

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
    get_api_keys_table,
    get_audit_table,
    get_lifecycle_state,
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
        api_keys = ddb.create_table(
            TableName="panakoes-test-api-keys",
            KeySchema=[{"AttributeName": "api_key_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "api_key_id", "AttributeType": "S"}
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
        for t in (audit, api_keys, lifecycle):
            t.wait_until_exists()

        api_keys.put_item(
            Item={
                "api_key_id": "key_active",
                "owner_id": "user_xyz",
                "status": "active",
            }
        )
        api_keys.put_item(
            Item={
                "api_key_id": "key_already_revoked",
                "owner_id": "user_xyz",
                "status": "revoked",
                "revoked_at": "2026-04-01T00:00:00Z",
            }
        )

        yield {
            "audit": audit,
            "api_keys": api_keys,
            "lifecycle_state": LifecycleStateStore(table=lifecycle),
        }


@pytest_asyncio.fixture
async def client_admin(
    aws_resources: dict[str, Any],
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_api_keys_table] = lambda: aws_resources["api_keys"]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
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
async def test_revoke_api_key_happy_path(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/api-keys/key_active/revoke",
        json=_body("op-1", "REVOKE KEY key_active", "incident response"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["api_key_id"] == "key_active"
    assert body["result"]["was_active"] is True

    item = aws_resources["api_keys"].get_item(Key={"api_key_id": "key_active"})[
        "Item"
    ]
    assert item["status"] == "revoked"
    assert item["revoked_reason"] == "incident response"


@pytest.mark.integration
async def test_revoke_api_key_already_revoked_was_active_false(
    client_admin: AsyncClient,
) -> None:
    """Re-revoking a revoked key still succeeds; was_active reports prior state."""
    response = await client_admin.post(
        "/api/v1/admin/api-keys/key_already_revoked/revoke",
        json=_body("op-2", "REVOKE KEY key_already_revoked", "double check"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["was_active"] is False


@pytest.mark.integration
async def test_revoke_api_key_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/api-keys/key_active/revoke",
        json=_body("op-3", "REVOKE KEY wrong", "x"),
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_revoke_api_key_unknown_returns_failed_envelope(
    client_admin: AsyncClient,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/api-keys/missing_key/revoke",
        json=_body("op-4", "REVOKE KEY missing_key", "x"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "not found" in body["error_message"].lower()


@pytest.mark.integration
async def test_revoke_api_key_idempotent_replay(
    client_admin: AsyncClient, aws_resources: dict[str, Any]
) -> None:
    body = _body("op-5", "REVOKE KEY key_active", "first")
    first = await client_admin.post(
        "/api/v1/admin/api-keys/key_active/revoke", json=body
    )
    assert first.status_code == 200
    first_audit_id = first.json()["audit_request_id"]

    second = await client_admin.post(
        "/api/v1/admin/api-keys/key_active/revoke",
        json=_body("op-5", "REVOKE KEY key_active", "second"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_audit_id
    assert len(aws_resources["audit"].scan().get("Items", [])) == 2


@pytest.mark.integration
async def test_revoke_api_key_unauthenticated_returns_401(
    aws_resources: dict[str, Any],
) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_api_keys_table] = lambda: aws_resources["api_keys"]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/api/v1/admin/api-keys/key_active/revoke",
                json=_body("op-6", "REVOKE KEY key_active", "x"),
            )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
