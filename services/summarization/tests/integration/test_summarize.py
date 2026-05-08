"""End-to-end integration tests for `POST /summarize`."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import make_token, seed_transcript


@pytest.mark.integration
async def test_summarize_round_trip_writes_s3_and_ddb(
    async_client: AsyncClient,
    audit_store: MemoryAuditStore,
) -> None:
    """The happy path returns a record and emits a `summarization.completed` audit event."""
    seed_transcript("user_alpha", "tx_001", "transcript body for testing")
    token = make_token(sub="user_alpha")
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "tx_001", "tier": "haiku"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcript_id"] == "tx_001"
    assert body["user_id"] == "user_alpha"
    assert body["tier"] == "haiku"
    assert body["model"] == "claude-haiku-4-5"
    assert body["s3_key"] == "summaries/user_alpha/tx_001.md"
    assert body["summary"]
    actions = [event.action for event in audit_store.events]
    assert "summarization.completed" in actions


@pytest.mark.integration
async def test_summarize_sonnet_tier_uses_sonnet_model(
    async_client: AsyncClient,
) -> None:
    """The `sonnet` tier resolves to `claude-sonnet-4-6` per CLAUDE.md."""
    seed_transcript("user_beta", "tx_xyz", "another transcript")
    token = make_token(sub="user_beta")
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "tx_xyz", "tier": "sonnet"},
    )
    assert response.status_code == 200
    assert response.json()["model"] == "claude-sonnet-4-6"


@pytest.mark.integration
async def test_summarize_returns_404_for_missing_transcript(
    async_client: AsyncClient,
    audit_store: MemoryAuditStore,
) -> None:
    """A missing transcript yields a 404 and a `summarization.failed` audit."""
    token = make_token(sub="user_gamma")
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "missing", "tier": "haiku"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "transcript not found"}
    failure_events = [e for e in audit_store.events if e.action == "summarization.failed"]
    assert len(failure_events) == 1
    assert failure_events[0].details["reason"] == "transcript_not_found"


@pytest.mark.integration
async def test_summarize_returns_404_for_cross_user_access(
    async_client: AsyncClient,
) -> None:
    """User B cannot summarize user A's transcript even when guessing the id."""
    seed_transcript("user_a", "shared", "user A body")
    token = make_token(sub="user_b")
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "shared", "tier": "haiku"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_summarize_requires_auth(async_client: AsyncClient) -> None:
    """Unauthenticated requests are rejected with a 401."""
    response = await async_client.post(
        "/summarize",
        json={"transcript_id": "anything", "tier": "haiku"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_summarize_rejects_invalid_token(async_client: AsyncClient) -> None:
    """A malformed bearer token is rejected with a 401."""
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": "Bearer not-a-jwt"},
        json={"transcript_id": "anything", "tier": "haiku"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_summarize_validates_request_body(async_client: AsyncClient) -> None:
    """Missing or malformed body fields surface as 422 from FastAPI's validator."""
    token = make_token(sub="user_alpha")
    response = await async_client.post(
        "/summarize",
        headers={"Authorization": f"Bearer {token}"},
        json={"transcript_id": "", "tier": "haiku"},
    )
    assert response.status_code == 422
