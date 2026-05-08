"""Integration tests for `/sessions` endpoints.

Exercises both the GSI-backed list path (UserSessionsIndex) and the
get-by-id path with cross-user 404 handling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_list_sessions_empty_for_new_user(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A new user has an empty list."""
    response = await async_client.get("/sessions", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.integration
async def test_list_sessions_returns_only_callers_records(
    async_client: AsyncClient,
    sessions_table: Any,
    seed_session: Any,
    make_token: Any,
) -> None:
    """User B does not see user A's sessions."""
    base = datetime.now(UTC)
    seed_session(
        sessions_table,
        user_id="user_a",
        session_id="s_a1",
        created_at=base,
    )
    seed_session(
        sessions_table,
        user_id="user_b",
        session_id="s_b1",
        created_at=base + timedelta(seconds=1),
    )
    token_a = make_token(sub="user_a", email="a@example.com", jti="sa")
    response = await async_client.get(
        "/sessions", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["session_id"] for item in body["items"]} == {"s_a1"}


@pytest.mark.integration
async def test_list_sessions_paginates_with_cursor(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    sessions_table: Any,
    seed_session: Any,
) -> None:
    """A small `limit` returns a cursor that fetches the next page."""
    base = datetime.now(UTC)
    for i in range(5):
        seed_session(
            sessions_table,
            user_id="user_test_123",
            session_id=f"s_{i:02d}",
            created_at=base + timedelta(seconds=i),
        )
    first = await async_client.get("/sessions?limit=2", headers=auth_headers)
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = await async_client.get(
        f"/sessions?limit=2&cursor={first_body['next_cursor']}",
        headers=auth_headers,
    )
    second_body = second.json()
    assert len(second_body["items"]) == 2
    first_ids = {item["session_id"] for item in first_body["items"]}
    second_ids = {item["session_id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
async def test_list_sessions_rejects_oversized_limit(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`limit=200` is rejected (max 100)."""
    response = await async_client.get("/sessions?limit=200", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_sessions_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/sessions")
    assert response.status_code == 401


@pytest.mark.integration
async def test_list_sessions_ignores_cursor_for_other_user(
    async_client: AsyncClient,
    sessions_table: Any,
    seed_session: Any,
    make_token: Any,
) -> None:
    """Cursor that points at another user's session is ignored, not 500."""
    base = datetime.now(UTC)
    seed_session(
        sessions_table,
        user_id="user_a",
        session_id="s_a",
        created_at=base,
    )
    seed_session(
        sessions_table,
        user_id="user_b",
        session_id="s_b",
        created_at=base + timedelta(seconds=1),
    )
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")
    response = await async_client.get(
        "/sessions?cursor=s_a",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["session_id"] for item in body["items"]} == {"s_b"}


@pytest.mark.integration
async def test_list_sessions_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful listing writes a `query.records_listed` audit event."""
    response = await async_client.get("/sessions", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.records_listed"
    assert events[0].resource_type == "session"
    assert events[0].source_service == "query-api"


@pytest.mark.integration
async def test_get_session_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    sessions_table: Any,
    seed_session: Any,
) -> None:
    """Owner fetches their own session successfully."""
    seed_session(
        sessions_table,
        user_id="user_test_123",
        session_id="s_42",
        status="active",
    )
    response = await async_client.get("/sessions/s_42", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s_42"
    assert body["status"] == "active"


@pytest.mark.integration
async def test_get_session_emits_audit_event(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    sessions_table: Any,
    seed_session: Any,
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful single-record fetch writes a `query.record_fetched` event."""
    seed_session(
        sessions_table,
        user_id="user_test_123",
        session_id="s_42",
    )
    response = await async_client.get("/sessions/s_42", headers=auth_headers)
    assert response.status_code == 200
    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "query.record_fetched"
    assert events[0].resource_type == "session"
    assert events[0].resource_id == "s_42"


@pytest.mark.integration
async def test_get_session_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/sessions/some-id")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_session_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/sessions/no-such-session",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_session_denies_cross_user_access(
    async_client: AsyncClient,
    sessions_table: Any,
    seed_session: Any,
    make_token: Any,
) -> None:
    """User B cannot read user A's session (returns 404, not 403)."""
    seed_session(sessions_table, user_id="user_a", session_id="s_a")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")
    response = await async_client.get(
        "/sessions/s_a",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_session_includes_ended_at_when_present(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    sessions_table: Any,
    seed_session: Any,
) -> None:
    """A finished session surfaces its `ended_at`."""
    base = datetime.now(UTC)
    seed_session(
        sessions_table,
        user_id="user_test_123",
        session_id="s_done",
        status="ended",
        created_at=base - timedelta(minutes=5),
        ended_at=base,
    )
    response = await async_client.get("/sessions/s_done", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ended_at"] is not None
