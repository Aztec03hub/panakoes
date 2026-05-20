"""Tests for the LifecycleWatcher + SpotDrainHandler."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from panakoes_transcriber_stream.lifecycle import LifecycleWatcher, SpotDrainHandler


@pytest.mark.asyncio
async def test_lifecycle_watcher_sets_event_on_disconnected(
    ddb_resource: Any, sessions_table: Any
) -> None:
    watcher = LifecycleWatcher(
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        ddb_resource=ddb_resource,
        poll_interval_seconds=0.05,
    )
    task = asyncio.create_task(watcher.watch())
    # Let the watcher do its first poll.
    await asyncio.sleep(0.1)
    sessions_table.update_item(
        Key={"session_id": "sess_test1234567890ab"},
        UpdateExpression="SET #s = :v",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":v": "disconnected"},
    )
    await asyncio.wait_for(task, timeout=2.0)
    assert watcher.event.is_set()
    assert watcher.last_status == "disconnected"


@pytest.mark.asyncio
async def test_lifecycle_watcher_treats_missing_row_as_disconnect(
    ddb_resource: Any, sessions_table: Any
) -> None:
    sessions_table.delete_item(Key={"session_id": "sess_test1234567890ab"})
    watcher = LifecycleWatcher(
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        ddb_resource=ddb_resource,
        poll_interval_seconds=0.05,
    )
    await asyncio.wait_for(watcher.watch(), timeout=2.0)
    assert watcher.event.is_set()


@pytest.mark.asyncio
async def test_lifecycle_watcher_stop_exits_cleanly(ddb_resource: Any, sessions_table: Any) -> None:
    watcher = LifecycleWatcher(
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        ddb_resource=ddb_resource,
        poll_interval_seconds=0.05,
    )
    task = asyncio.create_task(watcher.watch())
    await asyncio.sleep(0.1)
    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert not watcher.event.is_set()


@pytest.mark.asyncio
async def test_lifecycle_watcher_swallows_ddb_errors() -> None:
    class _BoomTable:
        def get_item(self, **_: Any) -> Any:
            raise RuntimeError("ddb down")

    class _BoomResource:
        def Table(self, _: str) -> Any:
            return _BoomTable()

    watcher = LifecycleWatcher(
        "t",
        "s",
        ddb_resource=_BoomResource(),
        poll_interval_seconds=0.02,
    )
    task = asyncio.create_task(watcher.watch())
    await asyncio.sleep(0.1)
    # Watcher must NOT have set event on transient errors.
    assert not watcher.event.is_set()
    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_spot_drain_handler_observes_200_response() -> None:
    handler = SpotDrainHandler(poll_interval_seconds=0.05)

    fake_response = MagicMock()
    fake_response.read.return_value = b'{"action": "terminate", "time": "2026-05-20T16:50:00Z"}'
    fake_response.__enter__ = lambda self: self  # type: ignore[assignment]
    fake_response.__exit__ = lambda *a: None  # type: ignore[assignment]

    with patch("panakoes_transcriber_stream.lifecycle.urlopen", return_value=fake_response):
        await asyncio.wait_for(handler.watch(), timeout=2.0)
    assert handler.event.is_set()
    assert handler.action_payload is not None
    assert "terminate" in handler.action_payload


@pytest.mark.asyncio
async def test_spot_drain_handler_404_keeps_polling() -> None:
    handler = SpotDrainHandler(poll_interval_seconds=0.02)

    def fake_urlopen(*_a: Any, **_kw: Any) -> Any:
        raise HTTPError(
            url=SpotDrainHandler.METADATA_URL,
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    with patch("panakoes_transcriber_stream.lifecycle.urlopen", side_effect=fake_urlopen):
        task = asyncio.create_task(handler.watch())
        await asyncio.sleep(0.1)
        assert not handler.event.is_set()
        handler.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_spot_drain_handler_urlerror_keeps_polling() -> None:
    handler = SpotDrainHandler(poll_interval_seconds=0.02)

    with patch(
        "panakoes_transcriber_stream.lifecycle.urlopen",
        side_effect=URLError("nope"),
    ):
        task = asyncio.create_task(handler.watch())
        await asyncio.sleep(0.1)
        assert not handler.event.is_set()
        handler.stop()
        await asyncio.wait_for(task, timeout=2.0)
