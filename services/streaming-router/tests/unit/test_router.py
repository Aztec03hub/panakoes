"""Unit tests for the streaming-router Lambda.

One test per route plus edge cases. moto-backed DynamoDB + SQS +
EventBridge so the real AWS SDK call paths are exercised; tests
assert against table reads + queue receives instead of mocking
boto3.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from panakoes_streaming_router import Router, lambda_handler
from tests.conftest import make_event

# ---------------------------------------------------------------------------
# $connect
# ---------------------------------------------------------------------------


def test_connect_writes_session_row(
    sessions_table: Any,
    frame_queue: str,
    events_client: Any,
) -> None:
    """$connect persists a session row with status=connecting + user_id."""
    events_client.create_event_bus(Name="panakoes-streaming")
    router = Router.from_env()
    event = make_event(route_key="$connect", connection_id="conn-42", user_id="u1")

    response = router.handle(event)

    assert response["statusCode"] == 200
    row = sessions_table.get_item(Key={"session_id": "conn-42"})["Item"]
    assert row["status"] == "connecting"
    assert row["connection_id"] == "conn-42"
    assert row["user_id"] == "u1"
    assert row["tenant_id"] == "tenant_xyz"
    assert "connected_at" in row


# ---------------------------------------------------------------------------
# $disconnect
# ---------------------------------------------------------------------------


def test_disconnect_updates_session_row(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """$disconnect flips the row to status=disconnected with a timestamp."""
    sessions_table.put_item(
        Item={
            "session_id": "conn-99",
            "status": "connecting",
            "connection_id": "conn-99",
            "user_id": "u1",
        }
    )
    router = Router.from_env()
    event = make_event(route_key="$disconnect", connection_id="conn-99")

    response = router.handle(event)

    assert response["statusCode"] == 200
    row = sessions_table.get_item(Key={"session_id": "conn-99"})["Item"]
    assert row["status"] == "disconnected"
    assert "disconnected_at" in row


def test_disconnect_is_idempotent_when_session_missing(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """$disconnect for an unknown session does not raise."""
    router = Router.from_env()
    event = make_event(route_key="$disconnect", connection_id="conn-ghost")

    response = router.handle(event)

    assert response["statusCode"] == 200


# ---------------------------------------------------------------------------
# audio-frame
# ---------------------------------------------------------------------------


def test_audio_frame_forwarded_to_sqs(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
) -> None:
    """audio-frame body lands on the per-session SQS queue."""
    router = Router.from_env()
    body = json.dumps({"action": "audio-frame", "data": {"pcm": "AAAA"}})
    event = make_event(route_key="audio-frame", connection_id="conn-7", body=body)

    response = router.handle(event)

    assert response["statusCode"] == 200
    received = sqs_client.receive_message(QueueUrl=frame_queue, MaxNumberOfMessages=10)
    messages = received.get("Messages", [])
    assert len(messages) == 1
    payload = json.loads(messages[0]["Body"])
    assert payload["session_id"] == "conn-7"
    assert payload["body"] == body


# ---------------------------------------------------------------------------
# transcript-request
# ---------------------------------------------------------------------------


def test_transcript_request_returns_last_transcript(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """transcript-request returns the row's last_transcript field."""
    sessions_table.put_item(
        Item={
            "session_id": "conn-77",
            "status": "active",
            "connection_id": "conn-77",
            "last_transcript": "hello world",
        }
    )
    router = Router.from_env()
    event = make_event(route_key="transcript-request", connection_id="conn-77", body="{}")

    response = router.handle(event)

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["transcript"] == "hello world"


def test_transcript_request_missing_session_returns_empty_transcript(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """transcript-request for an unknown session returns empty transcript."""
    router = Router.from_env()
    event = make_event(route_key="transcript-request", connection_id="conn-gone")

    response = router.handle(event)

    assert response["statusCode"] == 200
    payload = json.loads(response["body"])
    assert payload["transcript"] == ""


# ---------------------------------------------------------------------------
# $default
# ---------------------------------------------------------------------------


def test_default_route_logs_and_returns_200(
    sessions_table: Any,
    frame_queue: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown action lands on $default; we log + return 200."""
    router = Router.from_env()
    event = make_event(route_key="$default", connection_id="conn-x", body="{}")

    with caplog.at_level("WARNING"):
        response = router.handle(event)

    assert response["statusCode"] == 200
    assert any("$default" in record.message or "unknown" in record.message.lower()
               for record in caplog.records)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_lambda_handler_dispatches_to_router(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """lambda_handler is a thin shim over Router.handle."""
    event = make_event(route_key="$connect", connection_id="conn-shim")

    response = lambda_handler(event, None)

    assert response["statusCode"] == 200
    row = sessions_table.get_item(Key={"session_id": "conn-shim"})["Item"]
    assert row["status"] == "connecting"


def test_unknown_route_key_falls_through_to_default(
    sessions_table: Any,
    frame_queue: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An action API Gateway dispatched but the router does not know
    is treated like $default (log + 200)."""
    router = Router.from_env()
    event = make_event(route_key="unknown-future-action", connection_id="conn-fwd")

    with caplog.at_level("WARNING"):
        response = router.handle(event)

    assert response["statusCode"] == 200


def test_connect_without_authorizer_context_still_writes_row(
    sessions_table: Any,
    frame_queue: str,
) -> None:
    """If authorizer context is absent we still persist (placeholder values)."""
    router = Router.from_env()
    event = {
        "requestContext": {
            "routeKey": "$connect",
            "connectionId": "conn-noauth",
            "eventType": "CONNECT",
        }
    }

    response = router.handle(event)

    assert response["statusCode"] == 200
    row = sessions_table.get_item(Key={"session_id": "conn-noauth"})["Item"]
    assert row["user_id"] == ""
    assert row["tenant_id"] == ""


# ---------------------------------------------------------------------------
# Real-time observability: $connect status emit (router-accepted)
# ---------------------------------------------------------------------------


def _build_router_with_mock_ws_mgmt(
    *,
    sessions_table: Any,
    sqs_client: Any,
    events_client: Any,
    frame_queue_url: str,
    ws_mgmt_client: MagicMock,
) -> Router:
    """Build a Router that uses an in-memory mock for the management API."""
    return Router(
        sessions_table=sessions_table,
        sqs_client=sqs_client,
        events_client=events_client,
        audio_frame_queue_url=frame_queue_url,
        event_bus_name="default",
        ws_mgmt_endpoint="https://x.execute-api.us-east-1.amazonaws.com/dev",
        ws_mgmt_client=ws_mgmt_client,
    )


def test_connect_emits_router_accepted_status(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
    events_client: Any,
) -> None:
    """$connect posts a `{"type":"status","stage":"router-accepted"}` envelope."""
    events_client.create_event_bus(Name="panakoes-streaming")
    mgmt = MagicMock()
    router = _build_router_with_mock_ws_mgmt(
        sessions_table=sessions_table,
        sqs_client=sqs_client,
        events_client=events_client,
        frame_queue_url=frame_queue,
        ws_mgmt_client=mgmt,
    )
    event = make_event(route_key="$connect", connection_id="conn-status-1", user_id="u1")

    response = router.handle(event)

    assert response["statusCode"] == 200
    mgmt.post_to_connection.assert_called_once()
    kwargs = mgmt.post_to_connection.call_args.kwargs
    assert kwargs["ConnectionId"] == "conn-status-1"
    payload = json.loads(kwargs["Data"].decode("utf-8"))
    assert payload["type"] == "status"
    assert payload["stage"] == "router-accepted"
    assert payload["detail"] == "Session accepted; spawn dispatched"
    assert "ts" in payload


def test_connect_status_emit_disabled_when_endpoint_blank(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
    events_client: Any,
) -> None:
    """An empty `ws_mgmt_endpoint` disables emission. The session row + the
    EventBridge event still land; the management API is simply never called."""
    events_client.create_event_bus(Name="panakoes-streaming")
    mgmt = MagicMock()
    router = Router(
        sessions_table=sessions_table,
        sqs_client=sqs_client,
        events_client=events_client,
        audio_frame_queue_url=frame_queue,
        event_bus_name="default",
        ws_mgmt_endpoint="",
        ws_mgmt_client=mgmt,
    )
    event = make_event(route_key="$connect", connection_id="conn-status-2", user_id="u1")

    response = router.handle(event)

    assert response["statusCode"] == 200
    mgmt.post_to_connection.assert_not_called()
    # The session row still lands.
    row = sessions_table.get_item(Key={"session_id": "conn-status-2"})["Item"]
    assert row["status"] == "connecting"


def test_connect_status_emit_swallows_gone_exception(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
    events_client: Any,
) -> None:
    """A `GoneException` mid-emit must not break the WS handshake.

    API GW does not guarantee the management API can post to a
    connection that is still completing its $connect handshake; the
    common transient response is GoneException. The route handler
    MUST return 200 regardless.
    """
    events_client.create_event_bus(Name="panakoes-streaming")
    mgmt = MagicMock()
    gone = Exception("GoneException stub")
    gone.response = {"Error": {"Code": "GoneException", "Message": ""}}
    mgmt.post_to_connection.side_effect = gone
    router = _build_router_with_mock_ws_mgmt(
        sessions_table=sessions_table,
        sqs_client=sqs_client,
        events_client=events_client,
        frame_queue_url=frame_queue,
        ws_mgmt_client=mgmt,
    )
    event = make_event(route_key="$connect", connection_id="conn-status-gone", user_id="u1")

    response = router.handle(event)

    assert response["statusCode"] == 200
    mgmt.post_to_connection.assert_called_once()


def test_connect_status_emit_swallows_unexpected_failure(
    sessions_table: Any,
    frame_queue: str,
    sqs_client: Any,
    events_client: Any,
) -> None:
    """An unexpected mgmt-api exception must not break the WS handshake."""
    events_client.create_event_bus(Name="panakoes-streaming")
    mgmt = MagicMock()
    mgmt.post_to_connection.side_effect = RuntimeError("network down")
    router = _build_router_with_mock_ws_mgmt(
        sessions_table=sessions_table,
        sqs_client=sqs_client,
        events_client=events_client,
        frame_queue_url=frame_queue,
        ws_mgmt_client=mgmt,
    )
    event = make_event(route_key="$connect", connection_id="conn-status-x", user_id="u1")

    response = router.handle(event)

    assert response["statusCode"] == 200
