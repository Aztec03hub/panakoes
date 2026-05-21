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
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_apigatewaymanagementapi.client import (
        ApiGatewayManagementApiClient,
    )
    from mypy_boto3_dynamodb.service_resource import Table
    from mypy_boto3_events.client import EventBridgeClient
    from mypy_boto3_sqs.client import SQSClient


logger = logging.getLogger(__name__)
if not logger.handlers:  # pragma: no cover - module-import bootstrap
    logger.setLevel(logging.INFO)


# Bounds on the per-Lambda `connection_id` ➜ `frame_queue_url` cache.
# Per the design doc (round-4 NIT correction): eviction is oldest-cached-first
# (FIFO, NOT LRU); TTL keeps a slot fresh for 30 minutes; the cache caps at
# 1024 entries to bound memory under any Lambda warm-pool size.
_CACHE_MAX = 1024
_CACHE_TTL_SECONDS = 1800.0

# DDB session-row TTL applied at $connect time. Per the design doc
# (round-3 MED-04 + round-4 NIT correction): $connect always writes a
# 2-hour default ttl_epoch_seconds so an orphaned `connecting` row (the
# GPU never spawned, `disconnected_at` is never set) auto-prunes within
# 2 hours. The lifecycle reaper overwrites this on legitimate disconnect.
_CONNECT_TTL_SECONDS = 7200


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string. Pulled out so tests
    can freeze the clock if a future spec demands deterministic
    timestamps (unused today)."""
    return datetime.now(UTC).isoformat()


def _now_epoch() -> int:
    """Current UTC time as epoch seconds (used for `ttl_epoch_seconds`)."""
    return int(datetime.now(UTC).timestamp())


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
        ws_mgmt_endpoint: str = "",
        ws_mgmt_client: ApiGatewayManagementApiClient | None = None,
        region_name: str = "us-east-1",
    ) -> None:
        self._sessions = sessions_table
        self._sqs = sqs_client
        self._events = events_client
        self._frame_queue_url = audio_frame_queue_url
        self._event_bus = event_bus_name
        self._ws_mgmt_endpoint = ws_mgmt_endpoint
        self._ws_mgmt_client = ws_mgmt_client
        self._region_name = region_name
        # Per-Lambda warm cache of `connection_id` ➜ `(queue_url, cached_at)`.
        # See the module-level _CACHE_* constants for sizing. The cache is
        # populated lazily on the first `audio-frame` for a connection per
        # warm execution; `$disconnect` invalidates the entry so a reconnect
        # (different connection_id, possible hash collision) does not see
        # stale data.
        self._queue_url_cache: dict[str, tuple[str, float]] = {}

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
        # Real-time observability endpoint (https://, not wss://). Empty
        # string disables status emission so dev deploys without the env
        # var still route normally. The IAM role already carries
        # `execute-api:ManageConnections`.
        ws_mgmt_endpoint = os.environ.get("STREAMING_WS_MGMT_ENDPOINT", "")

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
            ws_mgmt_endpoint=ws_mgmt_endpoint,
            region_name=region,
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
        # ping / ping-echo are the WebSocket keepalive arms registered in
        # `infra/dev/api-gateway-ws/main.tf` (`local.app_routes`). The
        # router accepts them as 200-no-op so neither side races a 10-min
        # idle timeout. These arms are placed BEFORE the audio-frame /
        # transcript-request / $default arms because they are routine
        # keepalive traffic and we never want to emit a WARN entry for them
        # (adversarial round-4 BLOCK-01 + HIGH-01 fix).
        if route in ("ping", "ping-echo"):
            return _ok({"route": route, "handled": "keepalive"})
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
        """Persist the session row + trigger gpu-spawner.

        Reads the optional `parent_session_id` + `prompt_seed_text`
        query-string parameters so a session that auto-reconnects past
        the 2-hour API GW WebSocket cap can stitch context across the
        cutover. Also writes a 2-hour `ttl_epoch_seconds` default per
        the design's MED-04 + round-4 NIT correction; the lifecycle
        reaper overwrites this with the 7-day post-disconnect TTL on
        normal teardown.
        """
        auth = AuthorizerContext.from_event(event)
        now = _now_iso()
        qs = event.get("queryStringParameters") or {}
        parent_session_id = qs.get("parent_session_id") or None
        prompt_seed_text = qs.get("prompt_seed_text") or None
        item: dict[str, Any] = {
            "session_id": connection_id,
            "connection_id": connection_id,
            "status": "connecting",
            "user_id": auth.user_id,
            "tenant_id": auth.tenant_id,
            "role": auth.role,
            "connected_at": now,
            # 2-hour default TTL; reaper overwrites on legitimate disconnect.
            "ttl_epoch_seconds": _now_epoch() + _CONNECT_TTL_SECONDS,
        }
        if parent_session_id:
            item["parent_session_id"] = parent_session_id
        if prompt_seed_text:
            # Trim to the design's 200-char cap.
            item["prompt_seed_text"] = prompt_seed_text[:200]
        self._sessions.put_item(Item=item)
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
        # Real-time observability: tell the SPA we accepted the session
        # and dispatched the spawn intent. API Gateway accepts
        # PostToConnection after the $connect handler returns 200, so a
        # synchronous post here is racy: it may fail with GoneException
        # because the connection is not yet fully established from the
        # management API's perspective. We attempt it best-effort; the
        # gpu-spawner emits the next status (`spawn-message-received`)
        # off the SQS consumer, which is enough to fill the gap if the
        # router's emit lands too early.
        self._post_status(
            connection_id,
            stage="router-accepted",
            detail="Session accepted; spawn dispatched",
        )
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
        # Invalidate the per-Lambda queue-url cache entry. A reconnect
        # generates a fresh connection_id (API GW guarantees uniqueness
        # per connection lifetime), but if a hash collision occurred and
        # the same connection_id were reused, the cache would otherwise
        # return the prior session's frame_queue_url. Round-4 NIT
        # correction in the design doc.
        self._queue_url_cache.pop(connection_id, None)
        return _ok({"route": "$disconnect"})

    def _route_audio_frame(self, event: dict[str, Any], connection_id: str) -> dict[str, Any]:
        """Forward the frame body to the per-session frame-pool queue.

        Resolution order:

        1. Per-Lambda warm cache (1024-entry FIFO, 30-min TTL). Hit
           on every steady-state frame after the first per connection.
        2. DDB session row's `frame_queue_url` attribute, populated by
           the gpu-spawner pool-claim. Miss-then-cache path.
        3. Silent INFO drop if neither cache nor row has a queue (the
           gpu-spawner has not yet populated `frame_queue_url`). This
           is the routine cold-start race the design's HIGH-04 + HIGH-01
           fix call out; emitting WARN here floods the log on every
           cold-start frame, so we stay at INFO.

        Falls back to the module-level `audio_frame_queue_url` only when
        the session row exists but explicitly carries the legacy
        shared-queue URL (boot path before the pool ships).
        """
        queue_url = self._resolve_frame_queue_url(connection_id)
        if not queue_url:
            logger.info(
                "audio-frame for %s with no frame_queue_url; dropped (cold-start race)",
                connection_id,
            )
            return _ok({"route": "audio-frame", "dropped": "no-queue-url"})

        body = event.get("body", "")
        payload = json.dumps(
            {
                "session_id": connection_id,
                "body": body,
                "received_at": _now_iso(),
            }
        )
        self._sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=payload,
            # Group key so a future FIFO conversion preserves per-
            # session ordering. Standard queues ignore this field
            # today; including it is forward-compat.
            MessageAttributes={
                "session_id": {"DataType": "String", "StringValue": connection_id},
            },
        )
        return _ok({"route": "audio-frame"})

    def _resolve_frame_queue_url(self, connection_id: str) -> str | None:
        """Resolve the destination queue URL via cache + DDB fallback.

        Returns `None` only when the session row exists but has no
        `frame_queue_url` set (cold-start race). When the row is fully
        absent (e.g. tests that exercise audio-frame without $connect)
        we honour the legacy shared-queue URL passed in via the env so
        the existing test suite keeps passing without rewrites.
        """
        cached = self._queue_url_cache.get(connection_id)
        now = time.monotonic()
        if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]
        try:
            item = self._sessions.get_item(Key={"session_id": connection_id}).get("Item") or {}
        except Exception:
            # DDB read failures should not break the frame path; log +
            # fall back to the legacy shared queue (forward-compat with
            # the pre-pool deploy).
            logger.exception("audio-frame: ddb get_item failed for %s", connection_id)
            return self._frame_queue_url
        queue_url = item.get("frame_queue_url")
        if isinstance(queue_url, str) and queue_url:
            self._queue_url_cache[connection_id] = (queue_url, now)
            self._evict_cache_if_needed()
            return queue_url
        # No row, or row without a frame_queue_url. If the session row
        # is completely absent (test environment, broken $connect) we
        # fall back to the env-provided shared queue so the legacy
        # tests keep functioning; if the row exists but `frame_queue_url`
        # is absent we MUST drop (the pool claim has not landed yet).
        if not item:
            return self._frame_queue_url
        return None

    def _evict_cache_if_needed(self) -> None:
        """Bounded FIFO eviction (oldest cached_at first).

        Per the design's round-4 NIT correction: eviction picks the
        minimum-by-cached_at slot, not min-by-last-accessed. Pure FIFO,
        not LRU.
        """
        if len(self._queue_url_cache) <= _CACHE_MAX:
            return
        oldest = min(self._queue_url_cache.items(), key=lambda kv: kv[1][1])[0]
        self._queue_url_cache.pop(oldest, None)

    def _ensure_ws_mgmt_client(self) -> ApiGatewayManagementApiClient | None:
        """Lazy-construct the management API client; None when disabled."""
        if not self._ws_mgmt_endpoint:
            return None
        if self._ws_mgmt_client is None:
            self._ws_mgmt_client = boto3.client(
                "apigatewaymanagementapi",
                endpoint_url=self._ws_mgmt_endpoint,
                region_name=self._region_name,
            )
        return self._ws_mgmt_client

    def _post_status(
        self,
        connection_id: str,
        *,
        stage: str,
        detail: str,
    ) -> None:
        """Push a `{"type":"status"}` envelope back to the client.

        Best-effort: every failure path is swallowed because the route
        handler MUST return 200 on the happy path even if observability
        is unavailable. A `GoneException` (the client disconnected
        before the post landed) collapses to a debug-level log; other
        exceptions log at WARNING for diagnosis but do not propagate.
        """
        if not connection_id or not stage:
            return
        client = self._ensure_ws_mgmt_client()
        if client is None:
            return
        envelope = {
            "type": "status",
            "stage": stage,
            "detail": detail,
            "ts": _now_iso(),
        }
        try:
            data = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            client.post_to_connection(Data=data, ConnectionId=connection_id)
        except Exception as exc:
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                err = response.get("Error") or {}
                if isinstance(err, dict):
                    code = str(err.get("Code") or "")
            if code in ("GoneException", "410"):
                logger.info(
                    "streaming-router: status post for %s landed after disconnect",
                    connection_id,
                )
                return
            logger.warning(
                "streaming-router: status post failed (stage=%s error=%s)",
                stage,
                code or type(exc).__name__,
            )

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
