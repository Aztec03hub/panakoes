"""Integration tests for `GET /notifications/{ulid}`."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_get_notification_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Owner fetches their own notification successfully."""
    create = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "alice@example.com",
            "subject": "Welcome",
            "template": "welcome",
            "vars": {"name": "Alice"},
        },
    )
    notification_id = create.json()["notification_id"]
    response = await async_client.get(
        f"/notifications/{notification_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notification_id"] == notification_id
    assert body["channel"] == "email"
    assert body["status"] == "sent"


@pytest.mark.integration
async def test_get_notification_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.get("/notifications/some-id")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_notification_returns_404_for_unknown_id(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.get(
        "/notifications/01ARZ3NDEKTSV4RRFFQ69G5FAV",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_notification_denies_cross_user_access(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """User B cannot read user A's notification (returns 404, not 403)."""
    token_a = make_token(sub="user_a", email="a@example.com", jti="sa")
    token_b = make_token(sub="user_b", email="b@example.com", jti="sb")

    create = await async_client.post(
        "/notify/email",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "to": "alice@example.com",
            "subject": "secret",
            "template": "welcome",
            "vars": {"name": "Alice"},
        },
    )
    notification_id = create.json()["notification_id"]

    response = await async_client.get(
        f"/notifications/{notification_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404
