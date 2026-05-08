"""Integration tests for `GET /sessions`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


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
    make_token: Any,
) -> None:
    """User B's list does NOT contain user A's sessions."""
    token_a = make_token(sub="user_a", email="a@example.com", jti="sa")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")

    await async_client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={},
    )

    response = await async_client.get(
        "/sessions",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.integration
async def test_list_sessions_returns_records_for_owner(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The caller sees their own sessions."""
    for _ in range(3):
        await async_client.post("/sessions", headers=auth_headers, json={})

    response = await async_client.get("/sessions", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3


@pytest.mark.integration
async def test_list_sessions_paginates_with_cursor(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A small `limit` returns a cursor that fetches the next page."""
    for _ in range(5):
        await async_client.post("/sessions", headers=auth_headers, json={})

    first = await async_client.get("/sessions?limit=2", headers=auth_headers)
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = await async_client.get(
        f"/sessions?limit=2&cursor={first_body['next_cursor']}",
        headers=auth_headers,
    )
    assert second.status_code == 200
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
async def test_list_sessions_ignores_foreign_cursor(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """A cursor pointing at another user's session is silently ignored."""
    token_a = make_token(sub="user_a", email="a@a.a", jti="sa")
    token_b = make_token(sub="user_b", email="b@b.b", jti="sb")

    create = await async_client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={},
    )
    foreign_session_id = create.json()["session_id"]

    # User B passes user A's session_id as a cursor: list still works
    # (returns user B's empty list rather than leaking existence).
    response = await async_client.get(
        f"/sessions?cursor={foreign_session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []
