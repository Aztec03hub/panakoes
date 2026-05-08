"""Integration tests for `POST /spawn`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_spawn_happy_path_returns_instance_id_and_audits(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """A service-actor call launches an instance and emits an audit event."""
    response = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_01J", "user_id": "user_01J"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["instance_id"].startswith("i-")

    events = _audit_memory_store.events
    assert len(events) == 1
    event = events[0]
    assert event.action == "gpu_spawner.spawned"
    assert event.resource_type == "ec2_instance"
    assert event.resource_id == body["instance_id"]
    assert event.source_service == "gpu-spawner"
    assert event.actor_type == "service"
    assert event.details["session_id"] == "sess_01J"
    assert event.details["user_id"] == "user_01J"


@pytest.mark.integration
async def test_spawn_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.post(
        "/spawn",
        json={"session_id": "sess_x", "user_id": "user_x"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_spawn_rejects_user_actor_token(
    async_client: AsyncClient,
    user_auth_headers: dict[str, str],
) -> None:
    """User-actor tokens cannot spawn (compute-spawning authority is service-only)."""
    response = await async_client.post(
        "/spawn",
        headers=user_auth_headers,
        json={"session_id": "sess_x", "user_id": "user_x"},
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_spawn_rejects_bad_token(async_client: AsyncClient) -> None:
    """Garbage token => 401."""
    response = await async_client.post(
        "/spawn",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"session_id": "sess_x", "user_id": "user_x"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_spawn_rejects_missing_field(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
) -> None:
    """Missing required field => 422."""
    response = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={"session_id": "sess_x"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_spawn_rejects_extra_field(
    async_client: AsyncClient,
    service_auth_headers: dict[str, str],
) -> None:
    """Clients cannot smuggle extra fields into the request body."""
    response = await async_client.post(
        "/spawn",
        headers=service_auth_headers,
        json={
            "session_id": "sess_x",
            "user_id": "user_x",
            "instance_type": "p4d.24xlarge",
        },
    )
    assert response.status_code == 422
