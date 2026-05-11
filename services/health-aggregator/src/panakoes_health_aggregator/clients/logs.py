"""Async wrapper around boto3 CloudWatch Logs `filter_log_events`.

Used as a heartbeat heuristic: if the service emitted at least one
log event within the configured window, we consider it alive. If
the log group does not exist (a fresh deploy before the first log
group is created), the wrapper returns False rather than raising
`ResourceNotFoundException` so the aggregator can roll up to
"unknown" without spamming alerts.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from botocore.exceptions import ClientError


class LogsClientProtocol(Protocol):
    """The subset of boto3.client('logs') we use."""

    def filter_log_events(
        self,
        *,
        logGroupName: str,
        startTime: int,
        limit: int,
    ) -> dict[str, Any]:
        """boto3 logs FilterLogEvents."""


class LogsClient:
    """Async thin wrapper over CloudWatch Logs FilterLogEvents."""

    def __init__(self, client: LogsClientProtocol) -> None:
        self._client = client

    async def has_recent_event(
        self, log_group: str, window_seconds: int
    ) -> bool:
        """True iff at least one log event exists in the recent window.

        A missing log group is treated as "no recent event" (returns
        False) rather than an error, since dev environments routinely
        run services whose log group has not been provisioned yet.
        """
        start_ms = int((time.time() - window_seconds) * 1000)
        try:
            response = await asyncio.to_thread(
                self._client.filter_log_events,
                logGroupName=log_group,
                startTime=start_ms,
                limit=1,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                return False
            raise
        events = response.get("events") or []
        return len(events) > 0
