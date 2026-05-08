"""Integration tests for `DELETE /sessions/{session_id}`."""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_delete_session_returns_204_and_marks_errored(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Soft-delete sets status=errored, expires_at=now, emits audit event."""
    before = int(time.time())
    create = await async_client.post("/sessions", headers=auth_headers, json={})
    session_id = create.json()["session_id"]

    response = await async_client.delete(
        f"/sessions/{session_id}", headers=auth_headers
    )
    assert response.status_code == 204
    assert response.content == b""

    after = int(time.time())
    fetched = await async_client.get(f"/sessions/{session_id}", headers=auth_headers)
    assert fetched.status_code == 200
    record = fetched.json()
    assert record["status"] == "errored"
    # Generous bounds for slow CI; the row's TTL is "now-ish" by design.
    assert before - 5 <= record["expires_at"] <= after + 5

    deleted_events = [
        e for e in _audit_memory_store.events if e.action == "session.deleted"
    ]
    assert len(deleted_events) == 1
    assert deleted_events[0].details == {"previous_status": "starting"}


@pytest.mark.integration
async def test_delete_session_unknown_id_returns_404(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404."""
    response = await async_client.delete(
        "/sessions/sess_missing", headers=auth_headers
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_session_denies_cross_user(
    async_client: AsyncClient,
    make_token: Any,
) -> None:
    """User B cannot delete user A's session (404, not 403)."""
    token_a = make_token(sub="user_a", email="a@a.a", jti="sa")
    token_b = make_token(sub="user_b", email="b@b.b", jti="sb")

    create = await async_client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token_a}"},
        json={},
    )
    session_id = create.json()["session_id"]

    response = await async_client.delete(
        f"/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_session_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.delete("/sessions/sess_x")
    assert response.status_code == 401


@pytest.mark.integration
async def test_delete_session_is_idempotent_for_owner(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A second delete by the owner still succeeds (refreshes expires_at)."""
    create = await async_client.post("/sessions", headers=auth_headers, json={})
    session_id = create.json()["session_id"]

    first = await async_client.delete(f"/sessions/{session_id}", headers=auth_headers)
    assert first.status_code == 204
    second = await async_client.delete(f"/sessions/{session_id}", headers=auth_headers)
    assert second.status_code == 204
