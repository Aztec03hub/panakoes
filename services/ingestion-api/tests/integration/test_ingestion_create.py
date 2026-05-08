"""Integration tests for `POST /ingestion/audio`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_create_ingestion_happy_path(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Successful create returns ids + URL and writes the DynamoDB record + audit event."""
    response = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={
            "filename": "meeting.m4a",
            "content_type": "audio/mp4",
            "size_bytes": 1_024_000,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "ingestion_id" in body
    assert body["upload_url"].startswith("http")
    assert "expires_at" in body

    # Audit event recorded.
    events = _audit_memory_store.events
    assert len(events) == 1
    event = events[0]
    assert event.action == "ingestion.intent_created"
    assert event.resource_type == "ingestion"
    assert event.resource_id == body["ingestion_id"]
    assert event.source_service == "ingestion-api"
    assert event.actor_type == "user"


@pytest.mark.integration
async def test_create_ingestion_persists_record_with_pending_status(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The DynamoDB record exists after creation and starts as `pending`."""
    create = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={
            "filename": "voice.wav",
            "content_type": "audio/wav",
            "size_bytes": 4096,
        },
    )
    ingestion_id = create.json()["ingestion_id"]

    fetched = await async_client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["status"] == "pending"
    assert record["ingestion_id"] == ingestion_id
    assert record["filename"] == "voice.wav"
    assert record["content_type"] == "audio/wav"
    assert record["size_bytes"] == 4096
    assert record["s3_key"].startswith("audio/")
    assert record["s3_key"].endswith("/voice.wav")


@pytest.mark.integration
async def test_create_ingestion_requires_auth(
    async_client: AsyncClient,
) -> None:
    """Missing Authorization header => 401."""
    response = await async_client.post(
        "/ingestion/audio",
        json={"filename": "x.m4a", "content_type": "audio/mp4", "size_bytes": 1},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_ingestion_rejects_bad_token(
    async_client: AsyncClient,
) -> None:
    """A garbage Bearer token => 401."""
    response = await async_client.post(
        "/ingestion/audio",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"filename": "x.m4a", "content_type": "audio/mp4", "size_bytes": 1},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_ingestion_rejects_missing_field(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Missing required field => 422."""
    response = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={"filename": "x.m4a", "content_type": "audio/mp4"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_create_ingestion_rejects_invalid_size(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Zero-byte upload => 422."""
    response = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={"filename": "x.m4a", "content_type": "audio/mp4", "size_bytes": 0},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_create_ingestion_rejects_extra_field(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Clients cannot smuggle extra fields (e.g. `user_id`) into the request."""
    response = await async_client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={
            "filename": "x.m4a",
            "content_type": "audio/mp4",
            "size_bytes": 10,
            "user_id": "spoofed",
        },
    )
    assert response.status_code == 422
