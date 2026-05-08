"""Integration tests for `/ingestions` endpoints.

We exercise both the list endpoint (paginated, owner-scoped) and the
get-one endpoint (404 on miss and on cross-user access). The store is
real DynamoDB courtesy of moto per ADR-018.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_list_ingestions_empty_for_new_user(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A new user has an empty list."""
    response = await async_client.get("/ingestions", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.integration
async def test_list_ingestions_returns_only_callers_records(
    async_client: AsyncClient,
    ingestion_table: Any,
    seed_ingestion: Any,
    make_token: Any,
) -> None:
    """User B's list does NOT contain user A's records."""
    seed_ingestion(ingestion_table, user_id="user_a", ingestion_id="i_a1")
    seed_ingestion(ingestion_table, user_id="user_a", ingestion_id="i_a2")
    seed_ingestion(ingestion_table, user_id="user_b", ingestion_id="i_b1")

    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")
    response = await async_client.get(
        "/ingestions",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["ingestion_id"] for item in body["items"]} == {"i_b1"}


@pytest.mark.integration
async def test_list_ingestions_returns_records_for_owner(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    ingestion_table: Any,
    seed_ingestion: Any,
) -> None:
    """The caller sees their own records."""
    for i in range(3):
        seed_ingestion(
            ingestion_table,
            user_id="user_test_123",
            ingestion_id=f"i_{i}",
        )
    response = await async_client.get("/ingestions", headers=auth_headers)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 3


@pytest.mark.integration
async def test_list_ingestions_paginates_with_cursor(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    ingestion_table: Any,
    seed_ingestion: Any,
) -> None:
    """A small `limit` returns a cursor that fetches the next page."""
    for i in range(5):
        seed_ingestion(
            ingestion_table,
            user_id="user_test_123",
            ingestion_id=f"i_{i:02d}",
        )
    first = await async_client.get("/ingestions?limit=2", headers=auth_headers)
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = await async_client.get(
        f"/ingestions?limit=2&cursor={first_body['next_cursor']}",
        headers=auth_headers,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 2
    first_ids = {item["ingestion_id"] for item in first_body["items"]}
    second_ids = {item["ingestion_id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
async def test_list_ingestions_rejects_oversized_limit(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`limit=200` is rejected (max 100)."""
    response = await async_client.get("/ingestions?limit=200", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_ingestions_rejects_zero_limit(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`limit=0` is rejected (min 1)."""
    response = await async_client.get("/ingestions?limit=0", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_ingestions_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/ingestions")
    assert response.status_code == 401


@pytest.mark.integration
async def test_list_ingestions_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful listing writes a `query.records_listed` audit event."""
    response = await async_client.get("/ingestions", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.records_listed"
    assert events[0].resource_type == "ingestion"
    assert events[0].source_service == "query-api"


@pytest.mark.integration
async def test_get_ingestion_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    ingestion_table: Any,
    seed_ingestion: Any,
) -> None:
    """Owner fetches their own record successfully."""
    seed_ingestion(
        ingestion_table,
        user_id="user_test_123",
        ingestion_id="i_42",
    )
    response = await async_client.get("/ingestions/i_42", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ingestion_id"] == "i_42"


@pytest.mark.integration
async def test_get_ingestion_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    ingestion_table: Any,
    seed_ingestion: Any,
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful single-record fetch writes a `query.record_fetched` event."""
    seed_ingestion(
        ingestion_table,
        user_id="user_test_123",
        ingestion_id="i_42",
    )
    response = await async_client.get("/ingestions/i_42", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.record_fetched"
    assert events[0].resource_type == "ingestion"
    assert events[0].resource_id == "i_42"


@pytest.mark.integration
async def test_get_ingestion_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/ingestions/some-id")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_ingestion_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/ingestions/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_ingestion_denies_cross_user_access(
    async_client: AsyncClient,
    ingestion_table: Any,
    seed_ingestion: Any,
    make_token: Any,
) -> None:
    """User B cannot read user A's ingestion (returns 404, not 403)."""
    seed_ingestion(ingestion_table, user_id="user_a", ingestion_id="i_a")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")
    response = await async_client.get(
        "/ingestions/i_a",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_list_ingestions_orders_by_sort_key(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    ingestion_table: Any,
    seed_ingestion: Any,
) -> None:
    """Records come back ordered by sort key (ingestion id)."""
    base = datetime.now(UTC)
    for i in range(3):
        seed_ingestion(
            ingestion_table,
            user_id="user_test_123",
            ingestion_id=f"i_{i:02d}",
            created_at=base + timedelta(seconds=i),
        )
    response = await async_client.get("/ingestions", headers=auth_headers)
    assert response.status_code == 200
    ids = [item["ingestion_id"] for item in response.json()["items"]]
    assert ids == sorted(ids)
