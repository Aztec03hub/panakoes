"""Unit tests for Stage 2 streaming-router additions.

Covers:
- ping / ping-echo keepalive routes (BLOCK-01)
- queryStringParameters reads for parent_session_id + prompt_seed_text (CRIT-01)
- ttl_epoch_seconds default at $connect (MED-04 + round-4 NIT)
- Per-Lambda warm cache of connection_id ➜ frame_queue_url (CRIT-02)
- Cache invalidation on $disconnect (round-4 NIT)
- Cold-start race INFO drop when no frame_queue_url is on the row

The legacy `test_router.py` continues to assert the pre-Stage-2 happy
path; these tests cover the new behavior incrementally.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from panakoes_streaming_router import Router
from panakoes_streaming_router.router import _CACHE_MAX
from tests.conftest import make_event

# ---------------------------------------------------------------------------
# ping / ping-echo keepalive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route_key", ["ping", "ping-echo"])
def test_ping_routes_return_200_keepalive_without_warning(
    sessions_table: Any,
    frame_queue: str,
    caplog: pytest.LogCaptureFixture,
    route_key: str,
) -> None:
    """ping + ping-echo land as 200 keepalive with NO WARN emit."""
    router = Router.from_env()
    event = make_event(route_key=route_key, connection_id="conn-keepalive", body="{}")

    with caplog.at_level("WARNING"):
        response = router.handle(event)

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload == {"route": route_key, "handled": "keepalive"}
    # No WARN entries for keepalive traffic; that floods the log.
    assert not any(record.levelname == "WARNING" for record in caplog.records)


# ---------------------------------------------------------------------------
# $connect: queryStringParameters
# ---------------------------------------------------------------------------


def test_connect_persists_parent_session_id_from_query_string(
    sessions_table: Any,
    frame_queue: str,
    events_client: Any,
) -> None:
    """parent_session_id from queryStringParameters lands on the session row."""
    events_client.create_event_bus(Name="panakoes-streaming")
    router = Router.from_env()
    event = make_event(route_key="$connect", connection_id="conn-child")
    event["queryStringParameters"] = {
        "parent_session_id": "conn-parent",
        "prompt_seed_text": "...some prior tail",
    }

    router.handle(event)

    row = sessions_table.get_item(Key={"session_id": "conn-child"})["Item"]
    assert row["parent_session_id"] == "conn-parent"
    assert row["prompt_seed_text"] == "...some prior tail"


def test_connect_truncates_prompt_seed_to_200_chars(
    sessions_table: Any,
    frame_queue: str,
    events_client: Any,
) -> None:
    """prompt_seed_text longer than 200 chars is truncated."""
    events_client.create_event_bus(Name="panakoes-streaming")
    router = Router.from_env()
    long_seed = "x" * 500
    event = make_event(route_key="$connect", connection_id="conn-long")
    event["queryStringParameters"] = {"prompt_seed_text": long_seed}

    router.handle(event)

    row = sessions_table.get_item(Key={"session_id": "conn-long"})["Item"]
    assert len(row["prompt_seed_text"]) == 200


def test_connect_omits_parent_and_seed_when_absent(
    sessions_table: Any,
    frame_queue: str,
    events_client: Any,
) -> None:
    """Without query-string params, the row has no parent / seed attrs."""
    events_client.create_event_bus(Name="panakoes-streaming")
    router = Router.from_env()
    event = make_event(route_key="$connect", connection_id="conn-plain")

    router.handle(event)

    row = sessions_table.get_item(Key={"session_id": "conn-plain"})["Item"]
    assert "parent_session_id" not in row
    assert "prompt_seed_text" not in row


# ---------------------------------------------------------------------------
# $connect: ttl_epoch_seconds default
# ---------------------------------------------------------------------------


def test_connect_writes_ttl_epoch_seconds_two_hours_ahead(
    sessions_table: Any,
    frame_queue: str,
    events_client: Any,
) -> None:
    """The row gains a `ttl_epoch_seconds` ~2 hours ahead of connect time."""
    import time as _time

    events_client.create_event_bus(Name="panakoes-streaming")
    router = Router.from_env()
    event = make_event(route_key="$connect", connection_id="conn-ttl")
    before = int(_time.time())

    router.handle(event)

    after = int(_time.time())
    row = sessions_table.get_item(Key={"session_id": "conn-ttl"})["Item"]
    ttl = int(row["ttl_epoch_seconds"])
    # 7200 seconds = 2 hours. Allow generous bounds so wall-clock skew
    # between before/after does not flake the test.
    assert ttl >= before + 7000
    assert ttl <= after + 7400


# ---------------------------------------------------------------------------
# audio-frame: per-Lambda cache + cold-start drop
# ---------------------------------------------------------------------------


def _seed_row_with_queue(table: Any, *, connection_id: str, queue_url: str) -> None:
    """Helper: put a session row carrying frame_queue_url."""
    table.put_item(
        Item={
            "session_id": connection_id,
            "connection_id": connection_id,
            "status": "ready",
            "frame_queue_url": queue_url,
        }
    )


def test_audio_frame_reads_frame_queue_url_from_session_row(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
) -> None:
    """audio-frame fans out to the row's frame_queue_url, not the default."""
    # Create a SECOND queue and pin it on the row; the router should send to
    # this one, NOT the default `frame_queue` fixture.
    per_session = sqs_client.create_queue(QueueName="panakoes-test-per-session-q")["QueueUrl"]
    _seed_row_with_queue(sessions_table, connection_id="conn-pool", queue_url=per_session)
    router = Router.from_env()
    event = make_event(route_key="audio-frame", connection_id="conn-pool", body="{}")

    router.handle(event)

    msgs = sqs_client.receive_message(QueueUrl=per_session, MaxNumberOfMessages=10).get(
        "Messages", []
    )
    assert len(msgs) == 1
    # Default queue should NOT have a message.
    msgs_default = sqs_client.receive_message(QueueUrl=frame_queue, MaxNumberOfMessages=10).get(
        "Messages", []
    )
    assert msgs_default == []


def test_audio_frame_caches_queue_url_per_connection(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
) -> None:
    """Second audio-frame for the same connection skips DDB; uses cache."""
    per_session = sqs_client.create_queue(QueueName="panakoes-test-per-session-q2")["QueueUrl"]
    _seed_row_with_queue(sessions_table, connection_id="conn-warm", queue_url=per_session)
    router = Router.from_env()
    event = make_event(route_key="audio-frame", connection_id="conn-warm", body="{}")

    router.handle(event)
    router.handle(event)
    router.handle(event)

    msgs = sqs_client.receive_message(QueueUrl=per_session, MaxNumberOfMessages=10).get(
        "Messages", []
    )
    assert len(msgs) == 3
    # Cache should now hold an entry for the connection.
    assert "conn-warm" in router._queue_url_cache


def test_audio_frame_dropped_when_session_row_has_no_queue_url(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Row exists but frame_queue_url absent ➜ INFO drop, NOT WARN."""
    sessions_table.put_item(
        Item={
            "session_id": "conn-coldstart",
            "connection_id": "conn-coldstart",
            "status": "connecting",
        }
    )
    router = Router.from_env()
    event = make_event(route_key="audio-frame", connection_id="conn-coldstart", body="{}")

    with caplog.at_level("INFO"):
        response = router.handle(event)

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload.get("dropped") == "no-queue-url"
    # No WARN entries for the cold-start race; the log line lives at INFO
    # (design HIGH-01 round-2 fix).
    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_disconnect_invalidates_queue_url_cache(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
) -> None:
    """$disconnect removes the connection's cache entry."""
    per_session = sqs_client.create_queue(QueueName="panakoes-test-per-session-q3")["QueueUrl"]
    _seed_row_with_queue(sessions_table, connection_id="conn-evict", queue_url=per_session)
    router = Router.from_env()
    router.handle(make_event(route_key="audio-frame", connection_id="conn-evict", body="{}"))
    assert "conn-evict" in router._queue_url_cache

    router.handle(make_event(route_key="$disconnect", connection_id="conn-evict"))

    assert "conn-evict" not in router._queue_url_cache


def test_audio_frame_cache_evicts_oldest_when_over_limit(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
) -> None:
    """Cache stays at _CACHE_MAX; oldest cached_at is evicted first.

    Direct unit test on `_evict_cache_if_needed` to avoid populating
    1025 real DDB rows in a moto fixture.
    """
    router = Router.from_env()
    # Force-feed entries with deterministic monotonic timestamps.
    for i in range(_CACHE_MAX + 5):
        router._queue_url_cache[f"conn-{i}"] = (f"q-{i}", float(i))
    router._evict_cache_if_needed()
    # Should have shrunk to at most _CACHE_MAX; one eviction per call.
    assert len(router._queue_url_cache) == _CACHE_MAX + 4
    # The very oldest (conn-0, cached_at=0.0) should have been evicted.
    assert "conn-0" not in router._queue_url_cache
