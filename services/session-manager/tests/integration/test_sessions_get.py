"""Integration tests for `GET /sessions/{session_id}`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_get_session_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner fetches their own session successfully."""
    create = await async_client.post("/sessions", headers=auth_headers, json={})
    session_id = create.json()["session_id"]
    response = await async_client.get(f"/sessions/{session_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


@pytest.mark.integration
async def test_get_session_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/sessions/sess_anything")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_session_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/sessions/sess_does_not_exist", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_session_denies_cross_user_access(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """User B cannot read user A's session (returns 404, not 403)."""
    token_a = make_token(sub="user_a", email="a@example.com", jti="sess_a")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sess_b")

    create = await async_client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={},
    )
    session_id = create.json()["session_id"]

    response = await async_client.get(
        f"/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
