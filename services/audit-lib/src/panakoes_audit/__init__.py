"""Structured audit-event library for Panakoes services.

Every Python microservice imports this package to record audit events.
The library provides:

- `AuditEvent`: a strict Pydantic model that validates every event before
  it is persisted, so malformed events never reach the store.
- `AuditStore`: a Protocol describing the minimal write surface.
- Three concrete stores: `MemoryAuditStore` (tests),
  `StdoutAuditStore` (local dev, emits JSON to stdout),
  `DynamoDBAuditStore` (production, writes to DynamoDB).
- `record_event`: the public coroutine that consuming services call.

`record_event` lazily acquires the configured store on first call based
on the `AUDIT_BACKEND` environment variable; subsequent calls reuse it.
"""

from panakoes_audit._api import record_event, reset_store, set_store
from panakoes_audit.config import AuditSettings
from panakoes_audit.dynamodb import DynamoDBAuditStore
from panakoes_audit.event import AuditEvent
from panakoes_audit.store import AuditStore, MemoryAuditStore, StdoutAuditStore

__all__ = [
    "AuditEvent",
    "AuditSettings",
    "AuditStore",
    "DynamoDBAuditStore",
    "MemoryAuditStore",
    "StdoutAuditStore",
    "record_event",
    "reset_store",
    "set_store",
]
