"""DynamoDB cache layer for cost-api.

Wraps the `panakoes-dev-cost-cache` table with three operations:
- `get(key)` returns a cached `CostBreakdown` or None.
- `put(key, value, ttl_seconds=...)` writes a fresh entry with a TTL.
- `cache_or_fetch(key, fetch)` is the thin orchestrator the route layer
  calls: cache-hit returns the cached value with `cache_hit=True`, miss
  invokes the fetch callable, persists the result, and returns it with
  `cache_hit=False`.

DynamoDB TTL is best-effort and asynchronous (deletion happens within
~48h of the timestamp). That is acceptable here: a stale row past its
TTL is still safe to return because `get()` does not check `expires_at`
itself; we trust DynamoDB to sweep eventually. If sub-hour staleness
control is ever required, switch `get()` to evaluate `expires_at` before
returning. Phase 1 does not need that precision.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from panakoes_cost_api.models import CacheKey, CostBreakdown, utcnow

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


logger = structlog.get_logger(__name__)


DEFAULT_TTL_SECONDS = 3600  # 1 hour


class CostCache:
    """DynamoDB-backed cache for cost-api results.

    The `Table` resource is injected so tests can hand in a moto-mocked
    table without monkey-patching boto3. In production, the route layer
    constructs one `CostCache` per service lifetime and reuses it.
    """

    def __init__(self, table: Table) -> None:
        self._table = table

    def get(self, key: CacheKey) -> CostBreakdown | None:
        """Return the cached value for `key`, or None if absent."""
        cache_key = key.to_string()
        response = self._table.get_item(Key={"cache_key": cache_key})
        item = response.get("Item")
        if item is None:
            logger.debug("cost_cache_miss", cache_key=cache_key)
            return None
        payload = item.get("payload")
        if payload is None or not isinstance(payload, str):
            logger.warning("cost_cache_corrupt_row", cache_key=cache_key)
            return None
        breakdown = CostBreakdown.model_validate_json(payload)
        # The cache stores breakdowns produced with `cache_hit=False`. When
        # serving from cache we flip the flag so callers can distinguish.
        return breakdown.model_copy(update={"cache_hit": True})

    def put(
        self,
        key: CacheKey,
        value: CostBreakdown,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        """Persist `value` under `key` with `ttl_seconds` of freshness."""
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        cache_key = key.to_string()
        expires_at = int(utcnow().timestamp()) + ttl_seconds
        item: dict[str, Any] = {
            "cache_key": cache_key,
            "payload": value.model_dump_json(),
            "expires_at": expires_at,
        }
        self._table.put_item(Item=item)
        logger.debug(
            "cost_cache_put",
            cache_key=cache_key,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )

    async def cache_or_fetch(
        self,
        key: CacheKey,
        fetch: Callable[[], Awaitable[CostBreakdown]],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> CostBreakdown:
        """Return a cache hit, otherwise invoke `fetch`, persist, and return."""
        cached = self.get(key)
        if cached is not None:
            return cached
        fresh = await fetch()
        self.put(key, fresh, ttl_seconds=ttl_seconds)
        return fresh
