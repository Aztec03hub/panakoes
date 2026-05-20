"""Tests for the WsPublisher (boto3 client mocked)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from panakoes_transcriber_stream.ws_publisher import WsPublisher


def _gone_error() -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": "GoneException", "Message": "gone"}},
        operation_name="PostToConnection",
    )


def _server_error() -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": "InternalServerError", "Message": "boom"}},
        operation_name="PostToConnection",
    )


@pytest.mark.asyncio
async def test_send_serializes_payload_and_posts() -> None:
    fake_client = MagicMock()
    ws = WsPublisher(
        ws_endpoint="https://x.execute-api.us-east-1.amazonaws.com/dev",
        connection_id="cid-1",
        client=fake_client,
    )
    ok = await ws.send({"type": "ready"})
    assert ok is True
    fake_client.post_to_connection.assert_called_once()
    kwargs = fake_client.post_to_connection.call_args.kwargs
    assert kwargs["ConnectionId"] == "cid-1"
    assert kwargs["Data"] == b'{"type":"ready"}'


@pytest.mark.asyncio
async def test_send_410_marks_gone_and_fires_callback() -> None:
    fake_client = MagicMock()
    fake_client.post_to_connection.side_effect = _gone_error()
    callback_calls: list[int] = []

    async def on_gone() -> None:
        callback_calls.append(1)

    ws = WsPublisher("https://x/dev", "cid-1", client=fake_client, on_gone=on_gone)
    ok = await ws.send({"type": "ready"})
    assert ok is False
    assert ws.gone is True
    assert callback_calls == [1]

    # Subsequent send is a no-op; client is not called again.
    fake_client.post_to_connection.reset_mock()
    ok2 = await ws.send({"type": "ping"})
    assert ok2 is False
    fake_client.post_to_connection.assert_not_called()
    # Callback fires exactly once.
    assert callback_calls == [1]


@pytest.mark.asyncio
async def test_send_unknown_error_reraises() -> None:
    fake_client = MagicMock()
    fake_client.post_to_connection.side_effect = _server_error()
    ws = WsPublisher("https://x/dev", "cid-1", client=fake_client)
    with pytest.raises(ClientError):
        await ws.send({"type": "ready"})
    # Not marked gone on transient errors.
    assert ws.gone is False


@pytest.mark.asyncio
async def test_keepalive_pings_stop_on_gone() -> None:
    fake_client = MagicMock()
    # First send succeeds, second triggers GoneException.
    fake_client.post_to_connection.side_effect = [None, _gone_error()]
    ws = WsPublisher("https://x/dev", "cid-1", client=fake_client)
    task = asyncio.create_task(ws.keepalive_pings(interval_seconds=0.01))
    await asyncio.sleep(0.1)
    if not task.done():
        task.cancel()
    # Allow the task to exit cleanly.
    import contextlib

    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert ws.gone is True


@pytest.mark.asyncio
async def test_on_gone_callback_failure_does_not_propagate() -> None:
    fake_client = MagicMock()
    fake_client.post_to_connection.side_effect = _gone_error()

    async def bad_callback() -> None:
        raise RuntimeError("on-gone bombed")

    ws = WsPublisher("https://x/dev", "cid-1", client=fake_client, on_gone=bad_callback)
    # The bad callback must not raise out of send().
    ok = await ws.send({"type": "ready"})
    assert ok is False
    assert ws.gone is True


@pytest.mark.asyncio
async def test_ensure_client_lazy_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit client, the publisher lazy-builds via boto3."""

    fake_client = MagicMock()
    constructed_with: dict[str, Any] = {}

    def fake_boto3_client(service: str, **kwargs: Any) -> Any:
        constructed_with["service"] = service
        constructed_with.update(kwargs)
        return fake_client

    monkeypatch.setattr("panakoes_transcriber_stream.ws_publisher.boto3.client", fake_boto3_client)

    ws = WsPublisher("https://x/dev", "cid-1")
    ok = await ws.send({"type": "hi"})
    assert ok is True
    assert constructed_with["service"] == "apigatewaymanagementapi"
    assert constructed_with["endpoint_url"] == "https://x/dev"
