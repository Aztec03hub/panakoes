"""Integration tests for `GET /ingestion`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_list_ingestions_empty_for_new_user(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A new user has an empty list."""
    response = await async_client.get("/ingestion", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.integration
async def test_list_ingestions_returns_only_callers_records(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """User B's list does NOT contain user A's records."""
    token_a = make_token(sub="user_a", email="a@example.com", jti="sa")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")

    await async_client.post(
        "/ingestion/audio",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"filename": "alice.m4a", "content_type": "audio/mp4", "size_bytes": 1},
    )

    response = await async_client.get(
        "/ingestion",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.integration
async def test_list_ingestions_returns_records_for_owner(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The caller sees their own records."""
    for i in range(3):
        await async_client.post(
            "/ingestion/audio",
            headers=auth_headers,
            json={
                "filename": f"file{i}.m4a",
                "content_type": "audio/mp4",
                "size_bytes": 1,
            },
        )

    response = await async_client.get("/ingestion", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3


@pytest.mark.integration
async def test_list_ingestions_paginates_with_cursor(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A small `limit` returns a cursor that fetches the next page."""
    for i in range(5):
        await async_client.post(
            "/ingestion/audio",
            headers=auth_headers,
            json={
                "filename": f"f{i}.m4a",
                "content_type": "audio/mp4",
                "size_bytes": 1,
            },
        )

    first = await async_client.get("/ingestion?limit=2", headers=auth_headers)
    assert first.status_code == 200
    first_body = first.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second = await async_client.get(
        f"/ingestion?limit=2&cursor={first_body['next_cursor']}",
        headers=auth_headers,
    )
    assert second.status_code == 200
    second_body = second.json()
    assert len(second_body["items"]) == 2
    # No id collisions between pages.
    first_ids = {item["ingestion_id"] for item in first_body["items"]}
    second_ids = {item["ingestion_id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


@pytest.mark.integration
async def test_list_ingestions_rejects_oversized_limit(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`limit=200` is rejected (max 100)."""
    response = await async_client.get("/ingestion?limit=200", headers=auth_headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_ingestions_requires_auth(
    async_client: AsyncClient,
) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/ingestion")
    assert response.status_code == 401
