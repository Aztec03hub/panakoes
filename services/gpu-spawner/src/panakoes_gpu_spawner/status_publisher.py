"""Best-effort status emitter for the spawn pipeline.

The SPA `/realtime` page renders an event-log panel populated by
client-side observability events plus any server-pushed `status`
envelopes. This module wraps the API Gateway management-api client
(`apigatewaymanagementapi.PostToConnection`) so the gpu-spawner can
post per-stage progress updates back to the active WebSocket
connection while it spawns the GPU.

Design notes:

* Every emit is best-effort. A `GoneException` (the client already
  disconnected mid-spawn) collapses to a no-op; any other exception
  is logged but never propagated. Status posts MUST NOT break the
  underlying spawn flow.
* The publisher accepts an optional pre-built boto3 client so unit
  tests can substitute a `MagicMock`. Production callers leave it
  unset and let `boto3.client(...)` resolve the management API
  endpoint at first use.
* An empty `endpoint` string disables emission entirely. This keeps
  the spawner functional in unit / dev environments where the
  management URL is not wired through. Tests assert that no
  exception escapes the publisher in that disabled state.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import boto3

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (millisecond precision)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class StatusPublisher:
    """Pushes `{"type":"status", ...}` envelopes to a WS connection.

    Args:
        endpoint: full management URL,
            e.g. ``https://<api-id>.execute-api.us-east-1.amazonaws.com/dev``.
            An empty string disables emission (used in tests and in
            dev deploys that have not wired the endpoint yet).
        region_name: AWS region for the management-api client.
        client: optional pre-built boto3 client (test seam).
    """

    def __init__(
        self,
        *,
        endpoint: str,
        region_name: str = "us-east-1",
        client: Any | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._region_name = region_name
        self._client = client

    def _ensure_client(self) -> Any | None:
        """Lazy-resolve the boto3 client. Returns None if disabled."""
        if not self._endpoint:
            return None
        if self._client is None:
            self._client = boto3.client(
                "apigatewaymanagementapi",
                endpoint_url=self._endpoint,
                region_name=self._region_name,
            )
        return self._client

    def post(
        self,
        *,
        connection_id: str,
        stage: str,
        detail: str,
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        """Push one status envelope. Returns True on success, False otherwise.

        The boolean return is informational; callers SHOULD NOT branch
        on it. A failure here is never propagated; the spawn pipeline
        continues regardless.
        """
        if not connection_id or not stage:
            return False
        client = self._ensure_client()
        if client is None:
            return False
        envelope: dict[str, Any] = {
            "type": "status",
            "stage": stage,
            "detail": detail,
            "ts": _now_iso(),
        }
        if extra:
            for key, value in extra.items():
                # Reserved keys take precedence over caller-supplied
                # values so a buggy extra dict cannot rewrite the
                # envelope shape.
                if key in ("type", "stage", "detail", "ts"):
                    continue
                envelope[key] = value
        try:
            data = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            client.post_to_connection(Data=data, ConnectionId=connection_id)
            return True
        except Exception as exc:
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                err = response.get("Error") or {}
                if isinstance(err, dict):
                    code = str(err.get("Code") or "")
            if code in ("GoneException", "410"):
                # Client disconnected mid-spawn; expected during shutdown
                # races. Log at info level so it does not page on.
                logger.info(
                    "status_publisher_gone connection_id=%s stage=%s",
                    connection_id,
                    stage,
                )
                return False
            logger.warning(
                "status_publisher_post_failed connection_id=%s stage=%s error_code=%s",
                connection_id,
                stage,
                code or type(exc).__name__,
            )
            return False
