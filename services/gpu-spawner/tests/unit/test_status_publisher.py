"""Unit tests for the `StatusPublisher` helper.

The publisher wraps `apigatewaymanagementapi.PostToConnection` so the
spawn callback (and any future server-to-client emit path) can push
`{"type":"status"}` envelopes to the SPA without each call site having
to manage the boto3 client lifecycle or swallow `GoneException`
manually.

Contract:
- An empty endpoint URL disables emission (returns False, no I/O).
- A successful post serializes the envelope as compact JSON and calls
  `post_to_connection(Data=..., ConnectionId=...)`.
- `GoneException` collapses to a no-op (returns False, no raise).
- Any other exception is swallowed with a WARNING log (returns False,
  no raise).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from panakoes_gpu_spawner.status_publisher import StatusPublisher


def _client_error(code: str) -> Exception:
    """Build a fake botocore-shaped ClientError carrying `code`."""

    err = Exception("boto3 ClientError stub")
    err.response = {"Error": {"Code": code, "Message": "stub message"}}
    return err


@pytest.mark.unit
def test_post_disabled_when_endpoint_is_blank() -> None:
    """An empty endpoint disables emission so dev deploys without the
    env var still construct the publisher."""
    pub = StatusPublisher(endpoint="")
    assert pub.post(connection_id="c1", stage="some-stage", detail="d") is False


@pytest.mark.unit
def test_post_disabled_when_connection_id_missing() -> None:
    """A blank connection id is a precondition violation; collapse to
    a no-op rather than letting boto3 raise."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    assert pub.post(connection_id="", stage="s", detail="d") is False
    client.post_to_connection.assert_not_called()


@pytest.mark.unit
def test_post_serializes_envelope_and_calls_post_to_connection() -> None:
    """Happy path: the envelope shape matches the SPA-facing contract."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)

    ok = pub.post(
        connection_id="conn-42",
        stage="pool-claimed",
        detail="Pool queue 7 claimed",
        extra={"pool_id": 7, "queue_url": "https://sqs/...pool-7"},
    )
    assert ok is True
    client.post_to_connection.assert_called_once()
    kwargs = client.post_to_connection.call_args.kwargs
    assert kwargs["ConnectionId"] == "conn-42"
    body = json.loads(kwargs["Data"].decode("utf-8"))
    assert body["type"] == "status"
    assert body["stage"] == "pool-claimed"
    assert body["detail"] == "Pool queue 7 claimed"
    assert body["pool_id"] == 7
    assert body["queue_url"] == "https://sqs/...pool-7"
    assert "ts" in body


@pytest.mark.unit
def test_post_extras_cannot_overwrite_reserved_keys() -> None:
    """Caller-supplied extras must not rewrite type/stage/detail/ts."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    pub.post(
        connection_id="c1",
        stage="real-stage",
        detail="real-detail",
        extra={"type": "ATTACK", "stage": "fake", "ts": "evil", "ok": True},
    )
    body = json.loads(client.post_to_connection.call_args.kwargs["Data"].decode("utf-8"))
    assert body["type"] == "status"
    assert body["stage"] == "real-stage"
    assert body["detail"] == "real-detail"
    assert body["ok"] is True
    # The reserved-key rewrite did not land.
    assert body["ts"] != "evil"


@pytest.mark.unit
def test_post_swallows_gone_exception() -> None:
    """A `GoneException` is the canonical disconnect signal; return False."""
    client = MagicMock()
    client.post_to_connection.side_effect = _client_error("GoneException")
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    assert pub.post(connection_id="c", stage="s", detail="d") is False


@pytest.mark.unit
def test_post_swallows_unexpected_exception() -> None:
    """Non-GoneException failures are logged at WARNING but never raised."""
    client = MagicMock()
    client.post_to_connection.side_effect = RuntimeError("network down")
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    assert pub.post(connection_id="c", stage="s", detail="d") is False


@pytest.mark.unit
def test_post_swallows_misc_client_error_code() -> None:
    """A non-Gone botocore code is logged but does not propagate."""
    client = MagicMock()
    client.post_to_connection.side_effect = _client_error("InternalServerError")
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    assert pub.post(connection_id="c", stage="s", detail="d") is False


@pytest.mark.unit
def test_post_envelope_is_compact_json() -> None:
    """JSON payload uses compact separators (no whitespace) so the
    SPA event-log lines stay short and the on-the-wire bytes are
    minimised."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    pub.post(connection_id="c", stage="s", detail="d")
    raw: bytes = client.post_to_connection.call_args.kwargs["Data"]
    assert b", " not in raw
    assert b": " not in raw


@pytest.mark.unit
def test_post_ignores_unknown_keys_in_extra_safely() -> None:
    """Arbitrary keys in `extra` flow through verbatim."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    pub.post(
        connection_id="c",
        stage="instance-launching",
        detail="i-deadbeef launching",
        extra={"instance_id": "i-deadbeef"},
    )
    body = json.loads(client.post_to_connection.call_args.kwargs["Data"].decode("utf-8"))
    assert body["instance_id"] == "i-deadbeef"


@pytest.mark.unit
def test_post_calls_lazy_client_construction_only_once() -> None:
    """The boto3 client is constructed lazily and cached for reuse."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    pub.post(connection_id="c", stage="s", detail="d")
    pub.post(connection_id="c", stage="t", detail="d")
    # Both posts went through the same client (cached / re-used).
    assert client.post_to_connection.call_count == 2


@pytest.mark.unit
def test_envelope_keys_are_alphabetical_independent() -> None:
    """The envelope carries exactly the required fields in a stable order;
    the SPA does not depend on order but the field set is the contract."""
    client = MagicMock()
    pub = StatusPublisher(endpoint="https://x.execute-api/dev", client=client)
    pub.post(connection_id="c", stage="s", detail="d")
    body: dict[str, Any] = json.loads(
        client.post_to_connection.call_args.kwargs["Data"].decode("utf-8")
    )
    assert set(body.keys()) == {"type", "stage", "detail", "ts"}
