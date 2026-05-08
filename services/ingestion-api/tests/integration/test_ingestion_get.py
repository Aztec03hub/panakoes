"""Integration tests for `GET /ingestion/{ingestion_id}`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_get_ingestion_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner fetches their own record successfully."""
    create = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={"filename": "a.m4a", "content_type": "audio/mp4", "size_bytes": 1},
    )
    ingestion_id = create.json()["ingestion_id"]
    response = await async_client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ingestion_id"] == ingestion_id


@pytest.mark.integration
async def test_get_ingestion_requires_auth(
    async_client: AsyncClient,
) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/ingestion/some-id")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_ingestion_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/ingestion/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_ingestion_denies_cross_user_access(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """User B cannot read user A's ingestion (returns 404, not 403)."""
    token_a = make_token(sub="user_a", email="a@example.com", jti="sess_a")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sess_b")

    create = await async_client.post(
        "/ingestion/audio",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"filename": "secret.m4a", "content_type": "audio/mp4", "size_bytes": 1},
    )
    ingestion_id = create.json()["ingestion_id"]

    response = await async_client.get(
        f"/ingestion/{ingestion_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    # 404 (not 403): we do not leak the existence of other users' records.
    assert response.status_code == 404
