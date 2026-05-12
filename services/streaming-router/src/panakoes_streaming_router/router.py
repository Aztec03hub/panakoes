"""WebSocket streaming-router Lambda implementation.

The `Router` class wraps the AWS clients explicitly so tests can
inject moto-backed substitutes. `lambda_handler` is a thin shim
that constructs the router from env vars and delegates to it.

Route semantics live in `_route_*` methods. Each method is intentionally
small (one side-effect per route) so the test surface stays narrow.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table
    from mypy_boto3_events.client import EventBridgeClient
    from mypy_boto3_sqs.client import SQSClient


logger = logging.getLogger(__name__)
if not logger.handlers:  # pragma: no cover - module-import bootstrap
    logger.setLevel(logging.INFO)


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string. Pulled out so tests
    can freeze the clock if a future spec demands deterministic
    timestamps (unused today)."""
    return datetime.now(UTC).isoformat()


@dataclass
class AuthorizerContext:
    """The subset of the authorizer context the router consumes.

    Missing fields collapse to the empty string. The authorizer is
    responsible for permission decisions; the router only persists
    the metadata.
    """

    user_id: str
    tenant_id: str
    role: str

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> AuthorizerContext:
        ctx = event.get("requestContext", {})
        authorizer = ctx.get("authorizer") or {}
        # API Gateway v2 wraps Lambda-authorizer context under
        # `lambda` for REQUEST-type authorizers; older IAM authorizers
        # put it at the top level. We accept both shapes.
        lambda_ctx = authorizer.get("lambda") or authorizer
        return cls(
            user_id=str(lambda_ctx.get("user_id", "") or ""),
            tenant_id=str(lambda_ctx.get("tenant_id", "") or ""),
            role=str(lambda_ctx.get("role", "") or ""),
        )


class Router:
    """Dispatches WebSocket events to the right side-effect."""

    def __init__(
        self,
        *,
        sessions_table: Table,
        sqs_client: SQSClient,
        events_client: EventBridgeClient,
        audio_frame_queue_url: str,
        event_bus_name: str,
    ) -> None:
        self._sessions = sessions_table
        self._sqs = sqs_client
        self._events = events_client
        self._frame_queue_url = audio_frame_queue_url
        self._event_bus = event_bus_name

    @classmethod
    def from_env(cls) -> Router:
        """Construct a Router from the standard env vars + default clients."""
        table_name = os.environ.get("STREAMING_SESSIONS_TABLE")
        if not table_name:
            raise RuntimeError("STREAMING_SESSIONS_TABLE env var is required")
        queue_url = os.environ.get("AUDIO_FRAME_QUEUE_URL")
        if not queue_url:
            raise RuntimeError("AUDIO_FRAME_QUEUE_URL env var is required")
        region = os.environ.get("AWS_REGION", "us-east-1")
        bus = os.environ.get("STREAMING_EVENT_BUS", "default")

        ddb = boto3.resource("dynamodb", region_name=region)
        table = ddb.Table(table_name)
        sqs = boto3.client("sqs", region_name=region)
        events = boto3.client("events", region_name=region)

        return cls(
            sessions_table=table,
            sqs_client=sqs,
            events_client=events,
            audio_frame_queue_url=queue_url,
            event_bus_name=bus,
        )

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        """Dispatch by `requestContext.routeKey`."""
        ctx = event.get("requestContext", {})
        route = ctx.get("routeKey", "")
        connection_id = ctx.get("connectionId", "")

        if route == "$connect":
            return self._route_connect(event, connection_id)
        if route == "$disconnect":
            return self._route_disconnect(event, connection_id)
        if route == "audio-frame":
            return self._route_audio_frame(event, connection_id)
        if route == "transcript-request":
            return self._route_transcript_request(event, connection_id)
        # $default and any unknown action: log and accept (forward-
        # compat; an older deploy that does not know about a new
        # action should not blow up the WebSocket connection).
        logger.warning("streaming-router unknown route: %s", route)
        return _ok({"route": route, "handled": "default"})

    # -----------------------------------------------------------------
    # Route handlers
    # -----------------------------------------------------------------

    def _route_connect(self, event: dict[str, Any], connection_id: str) -> dict[str, Any]:
        """Persist the session row + trigger gpu-spawner."""
        auth = AuthorizerContext.from_event(event)
        now = _now_iso()
        self._sessions.put_item(
            Item={
                "session_id": connection_id,
                "connection_id": connection_id,
                "status": "connecting",
                "user_id": auth.user_id,
                "tenant_id": auth.tenant_id,
                "role": auth.role,
                "connected_at": now,
            }
        )
        # gpu-spawner subscribes to this event. We do not block the
        # WebSocket handshake on its reaction; spawning is async.
        try:
            self._events.put_events(
                Entries=[
                    {
                        "Source": "panakoes.streaming-router",
                        "DetailType": "streaming.session.connecting",
                        "Detail": json.dumps(
                            {
                                "session_id": connection_id,
                                "user_id": auth.user_id,
                                "tenant_id": auth.tenant_id,
                            }
                        ),
                        "EventBusName": self._event_bus,
                    }
                ]
            )
        except Exception:
            # EventBridge failure must not break the WS handshake;
            # session row is the source of truth. gpu-spawner reads
            # the table on a periodic sweep as a backstop.
            logger.warning("streaming-router: events.put_events failed", exc_info=True)
        return _ok({"route": "$connect"})

    def _route_disconnect(self, _event: dict[str, Any], connection_id: str) -> dict[str, Any]:
        """Update the session row to status=disconnected."""
        # Use UpdateItem so we do not clobber other fields; the row
        # may not exist if the $connect handler failed, in which case
        # the conditional update is a no-op.
        try:
            self._sessions.update_item(
                Key={"session_id": connection_id},
                UpdateExpression="SET #s = :s, disconnected_at = :t",
                ConditionExpression="attribute_exists(session_id)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":s": "disconnected",
                    ":t": _now_iso(),
                },
            )
        except Exception:
            logger.info(
                "streaming-router: disconnect for missing session %s (idempotent)",
                connection_id,
            )
        return _ok({"route": "$disconnect"})

    def _route_audio_frame(self, event: dict[str, Any], connection_id: str) -> dict[str, Any]:
        """Forward the frame body to the audio-frame SQS queue."""
        body = event.get("body", "")
        payload = json.dumps(
            {
                "session_id": connection_id,
                "body": body,
                "received_at": _now_iso(),
            }
        )
        self._sqs.send_message(
            QueueUrl=self._frame_queue_url,
            MessageBody=payload,
            # Group key so a future FIFO conversion preserves per-
            # session ordering. Standard queues ignore this field
            # today; including it is forward-compat.
            MessageAttributes={
                "session_id": {"DataType": "String", "StringValue": connection_id},
            },
        )
        return _ok({"route": "audio-frame"})

    def _route_transcript_request(
        self,
        _event: dict[str, Any],
        connection_id: str,
    ) -> dict[str, Any]:
        """Return the row's last_transcript field (stub for now).

        The GPU worker writes `last_transcript` to the session row on
        every partial flush. A future revision will switch this to a
        streaming-fetch from the live worker, but reading the row
        keeps the contract testable today.
        """
        item = self._sessions.get_item(Key={"session_id": connection_id}).get("Item") or {}
        transcript = item.get("last_transcript") or ""
        return _ok({"route": "transcript-request", "transcript": transcript})


def _ok(body: dict[str, Any]) -> dict[str, Any]:
    """Standard 200 response shape API Gateway WebSocket expects."""
    return {"statusCode": 200, "body": json.dumps(body)}


def lambda_handler(event: dict[str, Any], _context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint. Constructs a router and dispatches."""
    router = Router.from_env()
    return router.handle(event)
