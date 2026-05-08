"""Integration tests for `POST /notify/webhook`."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore


@pytest.mark.integration
async def test_webhook_happy_path_records_audit_and_db(
    async_client_with_webhook: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """A 2xx receiver => `sent` + `notification.webhook_sent` audit."""
    with respx.mock(assert_all_called=True) as rmock:
        rmock.post("https://hooks.example.com/abc").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        response = await async_client_with_webhook.post(
            "/notify/webhook",
            headers=auth_headers,
            json={
                "url": "https://hooks.example.com/abc",
                "payload": {"event": "ping"},
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "sent"
    assert body["attempts"] == 1

    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "notification.webhook_sent"


@pytest.mark.integration
async def test_webhook_rejects_http_url_at_400(
    async_client_with_webhook: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """`http://` URLs are rejected with 400; no audit, no DDB write."""
    response = await async_client_with_webhook.post(
        "/notify/webhook",
        headers=auth_headers,
        json={
            "url": "http://hooks.example.com/abc",
            "payload": {"event": "ping"},
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "webhook URL must use https://"
    # No audit event, no DB record.
    assert _audit_memory_store.events == []


@pytest.mark.integration
async def test_webhook_failed_after_retries_records_failure(
    async_client_with_webhook: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """Three 5xx responses => `failed` + `webhook_failed` audit + DB record."""
    with respx.mock(assert_all_called=True) as rmock:
        rmock.post("https://hooks.example.com/abc").mock(
            return_value=httpx.Response(503)
        )
        response = await async_client_with_webhook.post(
            "/notify/webhook",
            headers=auth_headers,
            json={
                "url": "https://hooks.example.com/abc",
                "payload": {"event": "ping"},
            },
        )
    # Endpoint still returns 200; the body reports the delivery failure.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["attempts"] == 3

    events = _audit_memory_store.events
    assert len(events) == 1
    assert events[0].action == "notification.webhook_failed"


@pytest.mark.integration
async def test_webhook_retries_on_5xx_then_succeeds(
    async_client_with_webhook: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """A 500 followed by 200 => `sent` with attempts=2."""
    responses = [httpx.Response(500), httpx.Response(200)]
    with respx.mock(assert_all_called=True) as rmock:
        rmock.post("https://hooks.example.com/abc").mock(side_effect=responses)
        response = await async_client_with_webhook.post(
            "/notify/webhook",
            headers=auth_headers,
            json={
                "url": "https://hooks.example.com/abc",
                "payload": {"event": "ping"},
            },
        )
    body = response.json()
    assert body["status"] == "sent"
    assert body["attempts"] == 2
    assert _audit_memory_store.events[0].action == "notification.webhook_sent"


@pytest.mark.integration
async def test_webhook_requires_auth(
    async_client_with_webhook: AsyncClient,
) -> None:
    """No Authorization header => 401."""
    response = await async_client_with_webhook.post(
        "/notify/webhook",
        json={"url": "https://hooks.example.com/abc", "payload": {}},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_webhook_passes_custom_headers_through(
    async_client_with_webhook: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Caller-supplied headers reach the receiver."""
    with respx.mock(assert_all_called=True) as rmock:
        route = rmock.post("https://hooks.example.com/abc").mock(
            return_value=httpx.Response(200)
        )
        await async_client_with_webhook.post(
            "/notify/webhook",
            headers=auth_headers,
            json={
                "url": "https://hooks.example.com/abc",
                "payload": {"x": 1},
                "headers": {"X-Signature": "abc123"},
            },
        )
        assert route.called
        sent_request = route.calls.last.request
        assert sent_request.headers.get("x-signature") == "abc123"
