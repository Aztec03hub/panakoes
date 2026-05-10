"""Thin EventBridge publisher used by Tier 3 lifecycle operations.

The wrapper exists for testability: the production class wraps a real
boto3 `events` client; a fake equivalent (`FakeEventBridgePublisher`)
captures calls in memory so integration tests can assert on the
emitted event payload without booting moto's events backend (moto
supports it but the in-memory fake is simpler and faster, and we are
not exercising EventBridge routing here, only the publish call).

The publisher returns the EventBridge `EventId` from the put_events
response so the operation's result envelope can surface it. Failures
to publish raise; the lifecycle orchestrator converts them into a
`status: "failed"` envelope per ADR-033.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from mypy_boto3_events.client import EventBridgeClient


logger = structlog.get_logger(__name__)


class EventBridgePublisher(Protocol):
    """Protocol satisfied by both production and fake publishers."""

    def put_event(
        self,
        *,
        source: str,
        detail_type: str,
        detail: dict[str, Any],
    ) -> str:
        """Publish a single event to the configured bus. Returns the EventId."""
        ...


class Boto3EventBridgePublisher:
    """Production EventBridge publisher backed by boto3."""

    def __init__(self, *, client: EventBridgeClient, bus_name: str) -> None:
        self._client = client
        self._bus_name = bus_name

    def put_event(
        self,
        *,
        source: str,
        detail_type: str,
        detail: dict[str, Any],
    ) -> str:
        """Publish a single event; raise if EventBridge rejects any entry."""
        import json

        response = self._client.put_events(
            Entries=[
                {
                    "Source": source,
                    "DetailType": detail_type,
                    "Detail": json.dumps(detail),
                    "EventBusName": self._bus_name,
                }
            ]
        )
        failed = response.get("FailedEntryCount", 0)
        if failed:
            entries = response.get("Entries", [])
            first_error = entries[0].get("ErrorMessage", "unknown") if entries else "unknown"
            raise RuntimeError(
                f"EventBridge put_events rejected {failed} entry: {first_error}"
            )
        entries = response.get("Entries", [])
        if not entries:
            raise RuntimeError("EventBridge put_events returned no entries")
        event_id = entries[0].get("EventId")
        if not event_id:
            raise RuntimeError("EventBridge put_events returned no EventId")
        logger.info(
            "tier3_eventbridge_publish",
            bus=self._bus_name,
            source=source,
            detail_type=detail_type,
            event_id=event_id,
        )
        return str(event_id)
