"""Integration tests for `POST /api/v1/admin/batch-jobs/{job_id}/kill`."""

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
from panakoes_admin_api.batch_client import BatchTerminator
from panakoes_admin_api.dependencies import (
    get_audit_table,
    get_batch_client,
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


class FakeBatchTerminator:
    """In-memory terminator that records calls. Configurable to raise."""

    def __init__(self, *, raise_for: set[str] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self._raise_for = raise_for or set()
        self._next_id = 0

    def terminate_job(self, *, job_id: str, reason: str) -> str:
        if job_id in self._raise_for:
            raise RuntimeError(f"Batch terminate failed: {job_id} not found")
        self._next_id += 1
        request_id = f"req-{self._next_id:04d}"
        self.calls.append({"job_id": job_id, "reason": reason, "request_id": request_id})
        return request_id


@pytest.fixture
def terminator() -> FakeBatchTerminator:
    return FakeBatchTerminator()


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
        lifecycle = ddb.create_table(
            TableName="panakoes-test-lifecycle-state",
            KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "idempotency_key", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for t in (audit, lifecycle):
            t.wait_until_exists()
        yield {
            "audit": audit,
            "lifecycle_state": LifecycleStateStore(table=lifecycle),
        }


@pytest_asyncio.fixture
async def client_admin(
    aws_resources: dict[str, Any], terminator: FakeBatchTerminator
) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    term: BatchTerminator = terminator
    app.dependency_overrides[get_batch_client] = lambda: term
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
async def test_kill_batch_job_happy_path(
    client_admin: AsyncClient, terminator: FakeBatchTerminator
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/batch-jobs/job_42/kill",
        json=_body("op-1", "KILL JOB job_42", "GPU node degraded"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["result"]["job_id"] == "job_42"
    assert body["result"]["killed_reason"] == "GPU node degraded"
    assert body["result"]["batch_terminate_request_id"] == "req-0001"
    assert terminator.calls == [
        {"job_id": "job_42", "reason": "GPU node degraded", "request_id": "req-0001"}
    ]


@pytest.mark.integration
async def test_kill_batch_job_wrong_confirmation_returns_400(
    client_admin: AsyncClient,
) -> None:
    response = await client_admin.post(
        "/api/v1/admin/batch-jobs/job_42/kill",
        json=_body("op-2", "KILL JOB nope", "x"),
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_kill_batch_job_terminator_raises_returns_failed_envelope(
    aws_resources: dict[str, Any],
) -> None:
    """If the batch client rejects (e.g. job not found), we get 200-failed."""
    terminator = FakeBatchTerminator(raise_for={"missing_job"})
    app.dependency_overrides[require_admin_with_step_up] = _admin_with_step_up
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    term: BatchTerminator = terminator
    app.dependency_overrides[get_batch_client] = lambda: term
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/api/v1/admin/batch-jobs/missing_job/kill",
                json=_body("op-3", "KILL JOB missing_job", "x"),
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert "batch terminate" in body["error_message"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
async def test_kill_batch_job_idempotent_replay(
    client_admin: AsyncClient,
    aws_resources: dict[str, Any],
    terminator: FakeBatchTerminator,
) -> None:
    body = _body("op-4", "KILL JOB job_42", "first")
    first = await client_admin.post(
        "/api/v1/admin/batch-jobs/job_42/kill", json=body
    )
    assert first.status_code == 200
    first_audit_id = first.json()["audit_request_id"]

    second = await client_admin.post(
        "/api/v1/admin/batch-jobs/job_42/kill",
        json=_body("op-4", "KILL JOB job_42", "second"),
    )
    assert second.status_code == 200
    assert second.json()["audit_request_id"] == first_audit_id
    assert len(terminator.calls) == 1


@pytest.mark.integration
async def test_kill_batch_job_unauthenticated_returns_401(
    aws_resources: dict[str, Any], terminator: FakeBatchTerminator
) -> None:
    app.dependency_overrides.clear()
    term: BatchTerminator = terminator
    app.dependency_overrides[get_audit_table] = lambda: aws_resources["audit"]
    app.dependency_overrides[get_lifecycle_state] = lambda: aws_resources[
        "lifecycle_state"
    ]
    app.dependency_overrides[get_batch_client] = lambda: term
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/api/v1/admin/batch-jobs/job_42/kill",
                json=_body("op-5", "KILL JOB job_42", "x"),
            )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
