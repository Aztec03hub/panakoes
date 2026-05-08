"""Integration tests for `GET /summary/{transcript_id}`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import make_token, seed_transcript


@pytest.mark.integration
async def test_get_summary_returns_record_for_owner(
    async_client: AsyncClient,
) -> None:
    """The owner can re-fetch a summary they previously created."""
    seed_transcript("u_owner", "tx_get", "body")
    token = make_token(sub="u_owner")
    create = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "tx_get", "tier": "haiku"},
    )
    assert create.status_code == 200
    fetched = await async_client.get(
        "/summary/tx_get",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["transcript_id"] == "tx_get"
    assert body["user_id"] == "u_owner"


@pytest.mark.integration
async def test_get_summary_returns_404_for_cross_user(
    async_client: AsyncClient,
) -> None:
    """A different user cannot fetch by the same transcript id."""
    seed_transcript("u_owner", "tx_priv", "body")
    owner_token = make_token(sub="u_owner")
    create = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"transcript_id": "tx_priv", "tier": "haiku"},
    )
    assert create.status_code == 200

    other_token = make_token(sub="u_other")
    fetched = await async_client.get(
        "/summary/tx_priv",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert fetched.status_code == 404


@pytest.mark.integration
async def test_get_summary_404s_when_missing(async_client: AsyncClient) -> None:
    """An unknown transcript id returns 404."""
    token = make_token(sub="u_owner")
    response = await async_client.get(
        "/summary/never_existed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_summary_requires_auth(async_client: AsyncClient) -> None:
    """Unauthenticated requests are rejected."""
    response = await async_client.get("/summary/anything")
    assert response.status_code == 401
