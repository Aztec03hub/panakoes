"""Session-row watcher + Spot 2-minute drain handler.

Two long-running watchers run alongside the SQS consumer; both set an
``asyncio.Event`` when their respective stop condition fires:

* ``LifecycleWatcher`` polls the DDB session row every few seconds. When
  it sees ``status == disconnected`` (or the row vanished), it sets
  ``event`` so the main loop can drain and exit cleanly.

* ``SpotDrainHandler`` polls the EC2 instance-metadata service at
  ``http://169.254.169.254/latest/meta-data/spot/instance-action`` every
  5 seconds. A 200 response means AWS has queued the instance for
  termination in ~2 minutes; we drain immediately.

Both watchers MUST be created BEFORE the (slow) ``backend_factory``
cold start so a ``$disconnect`` or Spot warning that arrives during the
35-second model-load window is observable. They idle-wait on their
respective polling cadences and consume essentially zero CPU.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

logger = logging.getLogger(__name__)


class LifecycleWatcher:
    """Polls the DDB session row for a ``disconnected`` status flip."""

    def __init__(
        self,
        sessions_table: str,
        session_id: str,
        *,
        ddb_resource: Any | None = None,
        poll_interval_seconds: float = 3.0,
    ) -> None:
        self._sessions_table_name = sessions_table
        self._session_id = session_id
        self._ddb_resource = ddb_resource
        self._poll_interval_seconds = poll_interval_seconds
        self.event = asyncio.Event()
        self._stop = asyncio.Event()
        self._last_status: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def _ensure_table(self) -> Any:
        if self._ddb_resource is None:
            self._ddb_resource = boto3.resource("dynamodb")
        return self._ddb_resource.Table(self._sessions_table_name)

    @property
    def last_status(self) -> str | None:
        return self._last_status

    async def watch(self) -> None:
        """Long-running poll loop; sets ``event`` and returns on disconnect."""

        loop = asyncio.get_running_loop()
        table = self._ensure_table()
        while not self._stop.is_set():
            try:
                resp = await loop.run_in_executor(
                    None,
                    lambda: table.get_item(Key={"session_id": self._session_id}),
                )
            except Exception:
                logger.exception(
                    "lifecycle_watcher_get_item_failed",
                    extra={"session_id": self._session_id},
                )
                await asyncio.sleep(self._poll_interval_seconds)
                continue

            item = resp.get("Item")
            if item is None:
                # The row vanished; treat as disconnect to avoid a
                # zombie container holding a GPU.
                logger.info(
                    "lifecycle_watcher_row_missing_treating_as_disconnect",
                    extra={"session_id": self._session_id},
                )
                self.event.set()
                return

            status = str(item.get("status", "")).strip().lower()
            self._last_status = status
            if status == "disconnected":
                logger.info(
                    "lifecycle_watcher_disconnect_observed",
                    extra={"session_id": self._session_id},
                )
                self.event.set()
                return

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
                return
            except TimeoutError:
                continue


class SpotDrainHandler:
    """Polls EC2 instance-metadata for the Spot-interruption warning."""

    METADATA_URL = "http://169.254.169.254/latest/meta-data/spot/instance-action"

    def __init__(self, *, poll_interval_seconds: float = 5.0) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self.event = asyncio.Event()
        self._stop = asyncio.Event()
        self._action_payload: str | None = None

    def stop(self) -> None:
        self._stop.set()

    @property
    def action_payload(self) -> str | None:
        return self._action_payload

    def _probe_once(self) -> str | None:
        """Synchronous IMDS probe; returns the body on 200, ``None`` otherwise."""

        try:
            req = Request(self.METADATA_URL)  # noqa: S310 (only http://169.254.169.254 IMDS)
            with urlopen(req, timeout=2.0) as resp:  # noqa: S310
                return resp.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            # 404 = no Spot warning queued; that is the steady-state.
            if exc.code != 404:
                logger.info(
                    "spot_drain_imds_http_error",
                    extra={"status": exc.code, "reason": str(exc.reason)},
                )
            return None
        except URLError:
            # Not running on EC2 (or the metadata service is unreachable).
            return None
        except Exception:
            logger.exception("spot_drain_imds_unexpected_error")
            return None

    async def watch(self) -> None:
        """Long-running probe loop; sets ``event`` on first 200 response."""

        loop = asyncio.get_running_loop()
        while not self._stop.is_set():
            payload = await loop.run_in_executor(None, self._probe_once)
            if payload is not None:
                logger.info(
                    "spot_drain_warning_observed",
                    extra={"payload": payload[:200]},
                )
                self._action_payload = payload
                self.event.set()
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
                return
            except TimeoutError:
                continue
