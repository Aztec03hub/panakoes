"""Factory that picks the right `AuditStore` from the environment.

Kept separate from `_api.py` so tests can exercise the selection logic
in isolation. `record_event` calls `_select_store_from_env` once on
first use; consuming code can override with `set_store` for tests.
"""

from __future__ import annotations

from panakoes_audit.config import AuditSettings
from panakoes_audit.dynamodb import DynamoDBAuditStore
from panakoes_audit.store import AuditStore, MemoryAuditStore, StdoutAuditStore


def _select_store_from_env(settings: AuditSettings | None = None) -> AuditStore:
    """Return the audit store dictated by the current environment.

    The `settings` parameter is exposed so tests can inject a
    pre-built `AuditSettings` without round-tripping through env vars.
    Production callers omit it.
    """
    cfg = settings if settings is not None else AuditSettings()
    if cfg.backend == "dynamodb":
        return DynamoDBAuditStore(
            table_name=cfg.table_name,
            region_name=cfg.aws_region,
        )
    if cfg.backend == "memory":
        return MemoryAuditStore()
    return StdoutAuditStore()
