"""Shared pytest fixtures for the panakoes-middleware library.

Two fixtures dominate test suites here:

- `memory_audit_store`: swaps the `panakoes-audit` global store with a
  `MemoryAuditStore` for the duration of the test, so audit-decorator
  tests can assert on emitted events without any IO.
- `clean_cors_env`: scrubs `CORS_*` env vars between tests so the
  env-driven CORS factory tests start from a known baseline.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from panakoes_audit import MemoryAuditStore, reset_store, set_store

CORS_ENV_KEYS = ("CORS_ALLOWED_ORIGINS",)


@pytest.fixture
def memory_audit_store() -> Iterator[MemoryAuditStore]:
    """Inject a `MemoryAuditStore` and reset the global between tests."""
    store = MemoryAuditStore()
    set_store(store)
    yield store
    reset_store()


@pytest.fixture(autouse=True)
def clean_cors_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove `CORS_*` env vars before every test."""
    for key in CORS_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield
