"""`AuditStore` protocol plus the in-memory and stdout backends.

A store is anything that can asynchronously persist an `AuditEvent`.
Keeping the surface as a Protocol means consumers can supply their own
backend (a Kafka producer, a structured logger, a file writer) and
slot it in via `set_store` without changing the public `record_event`
API.

The two stores defined here are the trivial ones:
- `MemoryAuditStore` for tests, which keeps events in a list.
- `StdoutAuditStore` for local dev, which prints one JSON line per
  event so CloudWatch / docker-compose log aggregators can ingest it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from panakoes_audit.event import AuditEvent


@runtime_checkable
class AuditStore(Protocol):
    """Minimal contract every audit backend implements.

    `record` MUST persist the event durably enough that the caller can
    treat its return as a commit. Backends that buffer (e.g. Kafka)
    should flush before returning, or document their durability model.
    """

    async def record(self, event: AuditEvent) -> None:
        """Persist a single audit event."""
        ...


class MemoryAuditStore:
    """In-memory audit store for unit tests and assertions.

    Tests construct one of these, inject it via `set_store`, exercise
    code paths that should record events, and then read `events` to
    assert the right things were captured. Call `clear()` to reset
    between tests.
    """

    def __init__(self) -> None:
        """Initialize with an empty event list."""
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> list[AuditEvent]:
        """Return the recorded events in insertion order.

        Returns the live list (not a copy) so tests can read it
        cheaply; mutate via `clear()` rather than poking the list.
        """
        return self._events

    def clear(self) -> None:
        """Remove all recorded events."""
        self._events.clear()

    async def record(self, event: AuditEvent) -> None:
        """Append `event` to the in-memory list."""
        self._events.append(event)


class StdoutAuditStore:
    """Audit store that emits one JSON line to stdout per event.

    Suitable for local development and for production tiers where
    CloudWatch agents pick up stdout. Pydantic's `model_dump_json`
    handles datetime serialization to ISO 8601 (with the UTC
    timezone preserved), which is the canonical wire format.
    """

    async def record(self, event: AuditEvent) -> None:
        """Print `event` as a single-line JSON document."""
        print(event.model_dump_json())
