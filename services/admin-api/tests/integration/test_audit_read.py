"""Integration tests for `GET /api/v1/admin/audit-log` (Tier 3.3 read view).

Exercises the full route stack against a moto-backed audit-log table
that mirrors the production schema (pk, sk, plus the `Tier3ActionIndex`
sparse GSI on `tier3_action`). Tests:

1. empty-result smoke
2. tier3_action filter returns only matching rows (sparse GSI proves
   itself by silently dropping non-Tier-3 rows)
3. cursor pagination across three pages (30 rows / limit=10)
4. non-admin role returns 403
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

from panakoes_admin_api.auth import get_jwt_claims, require_admin
from panakoes_admin_api.dependencies import get_audit_table
from panakoes_admin_api.main import app


def _admin_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_admin",
        iss="https://auth.example",
        aud="admin-api",
        iat=int(time.time()) - 60,
        exp=int(time.time()) + 3600,
        role="admin",
    )


def _non_admin_claims() -> JwtClaims:
    return JwtClaims(
        sub="user_member",
        iss="https://auth.example",
        aud="admin-api",
        iat=int(time.time()) - 60,
        exp=int(time.time()) + 3600,
        role="member",
    )


def _build_audit_table(name: str = "panakoes-test-audit-log") -> Any:
    """Build a moto-backed audit-log table including the Tier3ActionIndex GSI.

    Schema mirrors `infra/dev/data/main.tf`: composite primary key on
    (pk, sk), plus a sparse GSI on `tier3_action` (rows missing the
    attribute do not appear in the index).
    """
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    table = ddb.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
            {"AttributeName": "tier3_action", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "Tier3ActionIndex",
                "KeySchema": [
                    {"AttributeName": "tier3_action", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


def _put_tier3_row(
    table: Any,
    *,
    tier3_action: str,
    request_id: str,
    timestamp: str,
    actor_id: str = "user_admin",
    extra: dict[str, Any] | None = None,
) -> None:
    item: dict[str, Any] = {
        "pk": f"AUDIT#admin-api#{actor_id}",
        "sk": f"{timestamp}#{request_id}",
        "actor_id": actor_id,
        "action": f"tier3.{tier3_action}.intent",
        "tier3_action": tier3_action,
        "source_service": "admin-api",
        "request_id": request_id,
        "timestamp": timestamp,
        "outcome": "pending",
    }
    if extra:
        item.update(extra)
    table.put_item(Item=item)


def _put_non_tier3_row(table: Any, *, actor_id: str, request_id: str, timestamp: str) -> None:
    """Audit row without the `tier3_action` attribute. Sparse GSI ignores it."""
    table.put_item(
        Item={
            "pk": f"AUDIT#ingestion-api#{actor_id}",
            "sk": f"{timestamp}#{request_id}",
            "actor_id": actor_id,
            "action": "ingestion.upload",
            "source_service": "ingestion-api",
            "request_id": request_id,
            "timestamp": timestamp,
        }
    )


@pytest.fixture
def audit_table_fixture() -> Iterator[Any]:
    with mock_aws():
        table = _build_audit_table()
        yield table


@pytest_asyncio.fixture
async def client_admin(audit_table_fixture: Any) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[require_admin] = _admin_claims
    app.dependency_overrides[get_audit_table] = lambda: audit_table_fixture

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_audit_read_empty_returns_empty_page(client_admin: AsyncClient) -> None:
    """No rows in the table -> empty entries, null cursor, fresh timestamp."""
    response = await client_admin.get("/api/v1/admin/audit-log")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["entries"] == []
    assert body["next_cursor"] is None
    assert body["generated_at"]


@pytest.mark.integration
async def test_audit_read_tier3_action_filter_returns_only_matching(
    client_admin: AsyncClient, audit_table_fixture: Any
) -> None:
    """Filter by tier3_action returns only matching rows; non-Tier-3 rows excluded by sparse GSI."""
    _put_tier3_row(
        audit_table_fixture,
        tier3_action="terminate-session",
        request_id="r-1",
        timestamp="2026-05-09T00:00:00Z",
    )
    _put_tier3_row(
        audit_table_fixture,
        tier3_action="terminate-session",
        request_id="r-2",
        timestamp="2026-05-09T00:01:00Z",
    )
    _put_tier3_row(
        audit_table_fixture,
        tier3_action="force-fail-ingestion",
        request_id="r-3",
        timestamp="2026-05-09T00:02:00Z",
    )
    # Sparse GSI should silently drop this; proves it does not leak through.
    _put_non_tier3_row(
        audit_table_fixture,
        actor_id="user_other",
        request_id="r-4",
        timestamp="2026-05-09T00:03:00Z",
    )

    response = await client_admin.get(
        "/api/v1/admin/audit-log",
        params={"tier3_action": "terminate-session"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    actions = sorted(e["tier3_action"] for e in body["entries"])
    assert actions == ["terminate-session", "terminate-session"]
    request_ids = sorted(e["request_id"] for e in body["entries"])
    assert request_ids == ["r-1", "r-2"]
    # Payload carries the non-promoted attributes (e.g. action, outcome).
    assert all("outcome" in e["payload"] for e in body["entries"])
    assert body["next_cursor"] is None

    # Unfiltered read returns the three Tier 3 rows but NOT the non-Tier-3 one.
    unfiltered = await client_admin.get("/api/v1/admin/audit-log")
    assert unfiltered.status_code == 200
    request_ids_unfiltered = sorted(e["request_id"] for e in unfiltered.json()["entries"])
    assert request_ids_unfiltered == ["r-1", "r-2", "r-3"]


@pytest.mark.integration
async def test_audit_read_pagination_across_three_pages(
    client_admin: AsyncClient, audit_table_fixture: Any
) -> None:
    """30 rows + limit=10 -> walk three pages via cursor; final cursor is null."""
    total = 30
    action = "terminate-session"
    for i in range(total):
        _put_tier3_row(
            audit_table_fixture,
            tier3_action=action,
            request_id=f"r-{i:03d}",
            timestamp=f"2026-05-09T00:{i:02d}:00Z",
        )

    seen: list[str] = []
    cursor: str | None = None
    for page in range(3):
        params: dict[str, Any] = {"tier3_action": action, "limit": 10}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client_admin.get("/api/v1/admin/audit-log", params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["entries"]) == 10, f"page {page} returned {len(body['entries'])}"
        seen.extend(e["request_id"] for e in body["entries"])
        cursor = body["next_cursor"]
        if page < 2:
            assert cursor is not None, f"expected cursor after page {page}"

    # After three pages of 10 we should have walked every row exactly once.
    assert sorted(seen) == [f"r-{i:03d}" for i in range(total)]
    # Final cursor should be null (no more rows).
    assert cursor is None


@pytest.mark.integration
async def test_audit_read_non_admin_returns_403(
    audit_table_fixture: Any,
) -> None:
    """A token whose `role != 'admin'` is rejected by `require_admin` with 403."""
    # Override `get_jwt_claims` (one layer below `require_admin`) so the real
    # role check runs. This mirrors the pattern used in
    # `test_terminate_session.py`'s missing-auth case.
    app.dependency_overrides[get_jwt_claims] = _non_admin_claims
    app.dependency_overrides[get_audit_table] = lambda: audit_table_fixture
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.get("/api/v1/admin/audit-log")
        assert response.status_code == 403, response.text
        assert "admin" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
