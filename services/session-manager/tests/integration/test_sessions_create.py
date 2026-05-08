"""Integration tests for `POST /sessions`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_create_session_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful create returns the new record and writes audit + DDB."""
    response = await async_client.post(
        "/sessions",
        headers=auth_headers,
        json={"language": "en"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"].startswith("sess_")
    assert body["status"] == "starting"
    assert body["language"] == "en"
    assert body["gpu_instance_id"] is None
    assert body["expires_at"] > 0

    events = _audit_memory_store.events
    assert len(events) == 1
    event = events[0]
    assert event.action == "session.created"
    assert event.resource_type == "session"
    assert event.resource_id == body["session_id"]
    assert event.source_service == "session-manager"
    assert event.actor_type == "user"


@pytest.mark.integration
async def test_create_session_defaults_language_to_english(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """An empty body still creates a session, defaulting language to `en`."""
    response = await async_client.post("/sessions", headers=auth_headers, json={})
    assert response.status_code == 201
    assert response.json()["language"] == "en"


@pytest.mark.integration
async def test_create_session_persists_starting_status(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The new record is fetchable via GET right after create."""
    create = await async_client.post("/sessions", headers=auth_headers, json={})
    session_id = create.json()["session_id"]
    fetched = await async_client.get(f"/sessions/{session_id}", headers=auth_headers)
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["session_id"] == session_id
    assert record["status"] == "starting"


@pytest.mark.integration
async def test_create_session_requires_auth(async_client: AsyncClient) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.post("/sessions", json={})
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_session_rejects_bad_token(async_client: AsyncClient) -> None:
    """A garbage Bearer token => 401."""
    response = await async_client.post(
        "/sessions",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_session_rejects_extra_field(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Clients cannot smuggle extra fields (e.g. `user_id`) into the request."""
    response = await async_client.post(
        "/sessions",
        headers=auth_headers,
        json={"language": "en", "user_id": "spoofed"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_create_session_sets_ttl_around_eight_hours(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`expires_at` is roughly 8 hours after now (epoch seconds)."""
    import time

    before = int(time.time())
    response = await async_client.post("/sessions", headers=auth_headers, json={})
    assert response.status_code == 201
    expires_at = response.json()["expires_at"]
    eight_hours = 8 * 60 * 60
    # Allow generous slack for slow CI runners.
    assert before + eight_hours - 60 <= expires_at <= before + eight_hours + 60
