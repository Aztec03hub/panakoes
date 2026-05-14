"""Async wrapper around boto3 CloudWatch Logs `filter_log_events`."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from botocore.exceptions import ClientError

from panakoes_health_aggregator.models import ErrorEntry, LogEntry

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]


class LogsClientProtocol(Protocol):
    """The subset of boto3.client('logs') we use."""

    def filter_log_events(
        self,
        *,
        logGroupName: str,
        startTime: int,
        limit: int,
        filterPattern: str = "",
    ) -> dict[str, Any]:
        """boto3 logs FilterLogEvents."""
        ...


def _parse_level(message: str) -> LogLevel:
    """Best-effort level extraction from a structlog JSON log line."""
    try:
        data = json.loads(message)
        raw = str(data.get("level", "")).upper()
        if raw in ("DEBUG", "INFO", "WARN", "WARNING", "ERROR"):
            return "WARN" if raw == "WARNING" else raw  # type: ignore[return-value]
    except (json.JSONDecodeError, AttributeError):
        for level in ("ERROR", "WARN", "INFO", "DEBUG"):
            if level in message.upper():
                return level  # type: ignore[return-value]
    return "INFO"


class LogsClient:
    """Async wrapper over CloudWatch Logs FilterLogEvents."""

    def __init__(self, client: LogsClientProtocol) -> None:
        self._client = client

    def _start_ms(self, window_seconds: int) -> int:
        return int((time.time() - window_seconds) * 1000)

    async def has_recent_event(
        self, log_group: str, window_seconds: int
    ) -> bool:
        """True iff at least one log event exists in the recent window."""
        start_ms = self._start_ms(window_seconds)
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
        return len(response.get("events") or []) > 0

    async def get_recent_events(
        self,
        log_group: str,
        window_seconds: int = 3600,
        limit: int = 20,
    ) -> list[LogEntry]:
        """Return the most recent log entries from the last `window_seconds`."""
        start_ms = self._start_ms(window_seconds)
        try:
            response = await asyncio.to_thread(
                self._client.filter_log_events,
                logGroupName=log_group,
                startTime=start_ms,
                limit=limit,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                return []
            raise
        entries = []
        for ev in response.get("events") or []:
            msg = ev.get("message", "")
            entries.append(
                LogEntry(
                    timestamp=datetime.fromtimestamp(ev["timestamp"] / 1000, tz=UTC),
                    level=_parse_level(msg),
                    message=msg.strip(),
                )
            )
        return entries

    async def get_recent_errors(
        self,
        log_group: str,
        window_seconds: int = 86400,
        limit: int = 10,
    ) -> list[ErrorEntry]:
        """Return recent ERROR-level log entries as deduped ErrorEntry items."""
        start_ms = self._start_ms(window_seconds)
        try:
            response = await asyncio.to_thread(
                self._client.filter_log_events,
                logGroupName=log_group,
                startTime=start_ms,
                limit=limit,
                filterPattern="ERROR",
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                return []
            raise
        seen: dict[str, ErrorEntry] = {}
        for ev in response.get("events") or []:
            msg = ev.get("message", "").strip()
            ts = datetime.fromtimestamp(ev["timestamp"] / 1000, tz=UTC)
            if msg in seen:
                seen[msg] = ErrorEntry(
                    timestamp=max(seen[msg].timestamp, ts),
                    message=msg,
                    count=seen[msg].count + 1,
                )
            else:
                seen[msg] = ErrorEntry(timestamp=ts, message=msg, count=1)
        return sorted(seen.values(), key=lambda e: e.timestamp, reverse=True)
