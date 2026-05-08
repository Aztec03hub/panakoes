"""Unit tests for `panakoes_notification.clients.webhook.WebhookClient`."""

from __future__ import annotations

import httpx
import pytest
import respx

from panakoes_notification.clients.webhook import WebhookClient


@pytest.mark.unit
async def test_webhook_post_returns_success_on_2xx() -> None:
    """A 2xx response returns success on the first attempt."""
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("https://example.com/hook").mock(return_value=httpx.Response(200))
            client = WebhookClient(client=http, base_backoff_seconds=0.0)
            result = await client.post("https://example.com/hook", {"k": "v"})
    assert result.success is True
    assert result.attempts == 1
    assert result.status_code == 200
    assert result.error is None


@pytest.mark.unit
async def test_webhook_post_retries_on_5xx_then_succeeds() -> None:
    """A 500 followed by 200 retries and ultimately succeeds."""
    responses = [httpx.Response(500), httpx.Response(200)]
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("https://example.com/hook").mock(side_effect=responses)
            client = WebhookClient(client=http, base_backoff_seconds=0.0)
            result = await client.post("https://example.com/hook", {})
    assert result.success is True
    assert result.attempts == 2
    assert result.status_code == 200


@pytest.mark.unit
async def test_webhook_post_does_not_retry_on_4xx() -> None:
    """A 4xx response is final: do not retry."""
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=True) as rmock:
            route = rmock.post("https://example.com/hook").mock(
                return_value=httpx.Response(400)
            )
            client = WebhookClient(client=http, base_backoff_seconds=0.0)
            result = await client.post("https://example.com/hook", {})
            assert route.call_count == 1
    assert result.success is False
    assert result.attempts == 1
    assert result.status_code == 400
    assert result.error == "HTTP 400"


@pytest.mark.unit
async def test_webhook_post_exhausts_retries_on_persistent_5xx() -> None:
    """All attempts fail with 5xx => final failure with attempts=3."""
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("https://example.com/hook").mock(
                return_value=httpx.Response(503)
            )
            client = WebhookClient(
                client=http,
                max_attempts=3,
                base_backoff_seconds=0.0,
            )
            result = await client.post("https://example.com/hook", {})
    assert result.success is False
    assert result.attempts == 3
    assert result.status_code == 503


@pytest.mark.unit
async def test_webhook_post_handles_network_error() -> None:
    """A network-level error is captured in `result.error`."""
    async with httpx.AsyncClient() as http:
        with respx.mock(assert_all_called=True) as rmock:
            rmock.post("https://example.com/hook").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            client = WebhookClient(
                client=http,
                max_attempts=2,
                base_backoff_seconds=0.0,
            )
            result = await client.post("https://example.com/hook", {})
    assert result.success is False
    assert result.attempts == 2
    assert result.status_code is None
    assert result.error is not None
    assert "ConnectError" in result.error


@pytest.mark.unit
async def test_webhook_aclose_is_idempotent_on_owned_client() -> None:
    """Closing an owned client succeeds without raising."""
    client = WebhookClient(base_backoff_seconds=0.0)
    await client.aclose()


@pytest.mark.unit
async def test_webhook_aclose_skips_injected_client() -> None:
    """Closing a client we did not create leaves the injected client open."""
    async with httpx.AsyncClient() as http:
        client = WebhookClient(client=http, base_backoff_seconds=0.0)
        await client.aclose()
        assert not http.is_closed
