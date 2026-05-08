"""Integration tests for `GET /summaries`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import make_token, seed_transcript


@pytest.mark.integration
async def test_list_summaries_returns_only_owner_records(
    async_client: AsyncClient,
) -> None:
    """The list endpoint scopes results to the JWT subject."""
    owner_token = make_token(sub="u_owner")
    other_token = make_token(sub="u_other")
    for i in range(2):
        seed_transcript("u_owner", f"o{i}", "body")
        await async_client.post(
            "/summarize",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"transcript_id": f"o{i}", "tier": "haiku"},
        )
    seed_transcript("u_other", "x0", "other body")
    await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"transcript_id": "x0", "tier": "haiku"},
    )
    response = await async_client.get(
        "/summaries",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    transcript_ids = sorted(item["transcript_id"] for item in body["items"])
    assert transcript_ids == ["o0", "o1"]


@pytest.mark.integration
async def test_list_summaries_supports_pagination(
    async_client: AsyncClient,
) -> None:
    """A `limit` smaller than the result set yields a continuation cursor."""
    token = make_token(sub="u_paginator")
    for i in range(3):
        seed_transcript("u_paginator", f"p{i}", "body")
        await async_client.post(
            "/summarize",
            headers={"Authorization": f"Bearer {token}"},
            json={"transcript_id": f"p{i}", "tier": "haiku"},
        )
    page1 = await async_client.get(
        "/summaries",
        params={"limit": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    page2 = await async_client.get(
        "/summaries",
        params={"limit": 2, "cursor": body1["next_cursor"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None


@pytest.mark.integration
async def test_list_summaries_empty_for_new_user(async_client: AsyncClient) -> None:
    """A user with no summaries gets an empty list, not a 404."""
    token = make_token(sub="u_empty")
    response = await async_client.get(
        "/summaries",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


@pytest.mark.integration
async def test_list_summaries_requires_auth(async_client: AsyncClient) -> None:
    """Unauthenticated requests are rejected."""
    response = await async_client.get("/summaries")
    assert response.status_code == 401
