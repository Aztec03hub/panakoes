"""API Gateway WebSocket downstream emitter.

The container talks to the browser via the API Gateway management API
(``apigatewaymanagementapi.PostToConnection``). This module wraps the
boto3 client with three behaviors the rest of the service depends on:

* All payloads are JSON-serialized to UTF-8 bytes.
* A 410 ``GoneException`` is caught as the canonical "client
  disconnected" signal; the caller is expected to treat that as a
  ``$disconnect`` and drain. The 410 fact is surfaced via a callback
  so the main loop can break out and finalize.
* A periodic keepalive ping coroutine keeps the API Gateway 10-minute
  idle timer fresh during steady-state (design v7, CRIT-05).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Type alias for the 410 callback. The caller (main loop / drain handler)
# uses it to learn that the client has gone away.
GoneCallback = Callable[[], Awaitable[None]]


class WsPublisher:
    """Pushes JSON messages to a single API GW WS connection.

    Args:
        ws_endpoint: full management URL,
            e.g. ``https://<api-id>.execute-api.us-east-1.amazonaws.com/dev``.
        connection_id: the ``$connectionId`` value of the active session.
        client: optional pre-built boto3 client (test seam).
        on_gone: optional async callback fired the first time a
            ``PostToConnection`` returns 410. Subsequent 410s are
            swallowed; the callback fires exactly once per publisher.
    """

    def __init__(
        self,
        ws_endpoint: str,
        connection_id: str,
        *,
        client: Any | None = None,
        on_gone: GoneCallback | None = None,
    ) -> None:
        self._ws_endpoint = ws_endpoint
        self._connection_id = connection_id
        self._client = client
        self._on_gone = on_gone
        self._gone = False
        self._lock = asyncio.Lock()

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client("apigatewaymanagementapi", endpoint_url=self._ws_endpoint)
        return self._client

    @property
    def gone(self) -> bool:
        """True once a prior ``send`` has observed a 410 / GoneException."""

        return self._gone

    async def send(self, payload: dict[str, Any]) -> bool:
        """Push a JSON-encoded payload to the connected client.

        Returns ``True`` on success, ``False`` if the connection is
        already known-gone (no I/O attempted) or the post returned 410.
        Any other boto error is logged and re-raised.
        """

        if self._gone:
            return False

        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        client = self._ensure_client()
        loop = asyncio.get_running_loop()

        async with self._lock:
            if self._gone:
                return False
            try:
                await loop.run_in_executor(
                    None,
                    lambda: client.post_to_connection(Data=body, ConnectionId=self._connection_id),
                )
                return True
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                # API Gateway management API returns GoneException as a
                # ClientError; botocore exposes a GoneException class too
                # but only in some service models. Match by code string.
                if code in ("GoneException", "410"):
                    logger.info(
                        "ws_publisher_gone",
                        extra={"connection_id": self._connection_id},
                    )
                    await self._mark_gone()
                    return False
                logger.exception(
                    "ws_publisher_post_failed",
                    extra={
                        "connection_id": self._connection_id,
                        "error_code": code,
                    },
                )
                raise

    async def _mark_gone(self) -> None:
        if self._gone:
            return
        self._gone = True
        callback = self._on_gone
        if callback is not None:
            try:
                await callback()
            except Exception:
                logger.exception("ws_publisher_on_gone_failed")

    async def keepalive_pings(self, *, interval_seconds: float) -> None:
        """Periodically emit a ``ping`` message to refresh the API GW idle timer.

        Cancel the task when the main loop exits. The coroutine exits
        cleanly on the first 410 (``send`` returns ``False`` and we stop).
        """

        seq = 0
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                seq += 1
                ok = await self.send({"type": "ping", "seq": seq})
                if not ok:
                    logger.info(
                        "ws_publisher_keepalive_stopped",
                        extra={"reason": "gone", "last_seq": seq},
                    )
                    return
        except asyncio.CancelledError:
            return
