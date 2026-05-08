"""Integration tests for `/summaries` endpoints."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_list_summaries_empty_for_new_user(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A new user has an empty list."""
    response = await async_client.get("/summaries", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.integration
async def test_list_summaries_returns_only_callers_records(
    async_client: AsyncClient,
    summaries_table: Any,
    seed_summary: Any,
    make_token: Any,
) -> None:
    """User B does not see user A's summaries."""
    seed_summary(summaries_table, user_id="user_a", transcript_id="t_a1")
    seed_summary(summaries_table, user_id="user_b", transcript_id="t_b1")
    token_a = make_token(sub="user_a", email="a@example.com", jti="sa")
    response = await async_client.get(
        "/summaries", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["transcript_id"] for item in body["items"]} == {"t_a1"}


@pytest.mark.integration
async def test_list_summaries_paginates_with_cursor(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    summaries_table: Any,
    seed_summary: Any,
) -> None:
    """A small `limit` returns a cursor that fetches the next page."""
    for i in range(5):
        seed_summary(
            summaries_table,
            user_id="user_test_123",
            transcript_id=f"t_{i:02d}",
        )
    first = await async_client.get("/summaries?limit=2", headers=auth_headers)
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = await async_client.get(
        f"/summaries?limit=2&cursor={first_body['next_cursor']}",
        headers=auth_headers,
    )
    second_body = second.json()
    assert len(second_body["items"]) == 2
    first_ids = {item["transcript_id"] for item in first_body["items"]}
    second_ids = {item["transcript_id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
async def test_list_summaries_rejects_oversized_limit(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`limit=200` is rejected (max 100)."""
    response = await async_client.get("/summaries?limit=200", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_summaries_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/summaries")
    assert response.status_code == 401


@pytest.mark.integration
async def test_list_summaries_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful listing writes a `query.records_listed` audit event."""
    response = await async_client.get("/summaries", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.records_listed"
    assert events[0].resource_type == "summary"
    assert events[0].source_service == "query-api"


@pytest.mark.integration
async def test_get_summary_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    summaries_table: Any,
    seed_summary: Any,
) -> None:
    """Owner fetches their own summary successfully."""
    seed_summary(
        summaries_table,
        user_id="user_test_123",
        transcript_id="t_42",
        summary_text="A summary.",
    )
    response = await async_client.get("/summaries/t_42", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["transcript_id"] == "t_42"
    assert body["summary_text"] == "A summary."


@pytest.mark.integration
async def test_get_summary_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    summaries_table: Any,
    seed_summary: Any,
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful single-record fetch writes a `query.record_fetched` event."""
    seed_summary(
        summaries_table,
        user_id="user_test_123",
        transcript_id="t_42",
    )
    response = await async_client.get("/summaries/t_42", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.record_fetched"
    assert events[0].resource_type == "summary"
    assert events[0].resource_id == "t_42"


@pytest.mark.integration
async def test_get_summary_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/summaries/some-id")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_summary_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/summaries/no-such-transcript",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_summary_denies_cross_user_access(
    async_client: AsyncClient,
    summaries_table: Any,
    seed_summary: Any,
    make_token: Any,
) -> None:
    """User B cannot read user A's summary (returns 404, not 403)."""
    seed_summary(summaries_table, user_id="user_a", transcript_id="t_a")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")
    response = await async_client.get(
        "/summaries/t_a",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
