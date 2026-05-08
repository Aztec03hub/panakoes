"""Public `record_event` coroutine and the lazy global store.

Consuming services normally call `record_event(...)` once per audit
moment and let the library handle store selection, validation, and
serialization. For tests, services swap the global store using
`set_store(MemoryAuditStore())` and reset it with `reset_store()`.
"""

from __future__ import annotations

from typing import Any

from panakoes_audit.event import AuditEvent
from panakoes_audit.factory import _select_store_from_env
from panakoes_audit.store import AuditStore

_store: AuditStore | None = None


def set_store(store: AuditStore) -> None:
    """Override the active audit store.

    Used in tests to inject a `MemoryAuditStore`, or in advanced setups
    to plug in a custom backend without going through env vars. The
    store stays set until `reset_store()` is called or the process
    exits.
    """
    global _store
    _store = store


def reset_store() -> None:
    """Forget the active audit store so the next call re-resolves it.

    Mostly useful in test teardown after `set_store`.
    """
    global _store
    _store = None


def _get_store() -> AuditStore:
    """Return the active audit store, resolving from env on first call."""
    global _store
    if _store is None:
        _store = _select_store_from_env()
    return _store


async def record_event(
    *,
    actor_id: str,
    actor_type: str,
    action: str,
    resource_type: str,
    resource_id: str,
    source_service: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> AuditEvent:
    """Build and persist an `AuditEvent` via the configured store.

    Returns the validated event so callers can correlate downstream
    work with the same `request_id` that was recorded. All keyword-only
    arguments are forwarded to `AuditEvent`; their semantics match the
    model.
    """
    payload: dict[str, Any] = {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "source_service": source_service,
        "details": details if details is not None else {},
        "source_ip": source_ip,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    event = AuditEvent(**payload)
    await _get_store().record(event)
    return event
