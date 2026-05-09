"""Unit tests for the DynamoDB-backed `CostCache`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import boto3
import pytest
from moto import mock_aws

from panakoes_cost_api.cache import DEFAULT_TTL_SECONDS, CostCache
from panakoes_cost_api.models import CacheKey, CostBreakdown, CostByService


@pytest.fixture
def cache_table() -> Iterator[CostCache]:
    """Create a moto-backed cost-cache table and return a `CostCache` against it."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-cost-cache",
            KeySchema=[{"AttributeName": "cache_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "cache_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield CostCache(table=table)


def _sample_breakdown() -> CostBreakdown:
    return CostBreakdown(
        from_date=date(2026, 4, 1),
        to_date=date(2026, 5, 1),
        currency="USD",
        services=(
            CostByService(service="Amazon EC2", cost_cents=10000, percent_of_total=80.0),
            CostByService(service="Amazon S3", cost_cents=2500, percent_of_total=20.0),
        ),
        total_cents=12500,
        cache_hit=False,
        queried_at=datetime(2026, 4, 30, 14, 32, 18, tzinfo=UTC),
    )


def _sample_key() -> CacheKey:
    return CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))


@pytest.mark.unit
def test_cache_miss_returns_none(cache_table: CostCache) -> None:
    """`get(missing_key)` returns None cleanly without raising."""
    assert cache_table.get(_sample_key()) is None


@pytest.mark.unit
def test_cache_put_then_get_roundtrip(cache_table: CostCache) -> None:
    """Put then get returns the same breakdown with `cache_hit=True`."""
    key = _sample_key()
    value = _sample_breakdown()
    cache_table.put(key, value)
    fetched = cache_table.get(key)
    assert fetched is not None
    assert fetched.total_cents == value.total_cents
    assert fetched.services == value.services
    assert fetched.cache_hit is True  # cache flips this on read


@pytest.mark.unit
def test_cache_expires_at_is_one_hour_default(cache_table: CostCache) -> None:
    """Default put writes `expires_at = now + 3600`."""
    key = _sample_key()
    cache_table.put(key, _sample_breakdown())
    raw = cache_table._table.get_item(Key={"cache_key": key.to_string()})
    item = raw.get("Item")
    assert item is not None
    assert "expires_at" in item
    now_seconds = int(datetime.now(UTC).timestamp())
    expires_at = int(item["expires_at"])
    # Allow a generous 60s slop for slow test machines.
    assert DEFAULT_TTL_SECONDS - 60 <= expires_at - now_seconds <= DEFAULT_TTL_SECONDS + 60


@pytest.mark.unit
def test_cache_put_rejects_zero_or_negative_ttl(cache_table: CostCache) -> None:
    """A zero or negative TTL raises rather than silently writing a stale row."""
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        cache_table.put(_sample_key(), _sample_breakdown(), ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        cache_table.put(_sample_key(), _sample_breakdown(), ttl_seconds=-1)


@pytest.mark.unit
async def test_cache_or_fetch_uses_cache_on_hit(cache_table: CostCache) -> None:
    """A cache hit means the fetch callable is never invoked."""
    key = _sample_key()
    cache_table.put(key, _sample_breakdown())
    invocation_count = 0

    async def fetch() -> CostBreakdown:
        nonlocal invocation_count
        invocation_count += 1
        return _sample_breakdown()

    result = await cache_table.cache_or_fetch(key, fetch)
    assert invocation_count == 0
    assert result.cache_hit is True


@pytest.mark.unit
async def test_cache_or_fetch_falls_back_to_ce_on_miss(cache_table: CostCache) -> None:
    """A cache miss invokes fetch, persists the result, and returns it as fresh."""
    key = _sample_key()
    invocation_count = 0

    async def fetch() -> CostBreakdown:
        nonlocal invocation_count
        invocation_count += 1
        return _sample_breakdown()

    first = await cache_table.cache_or_fetch(key, fetch)
    assert invocation_count == 1
    assert first.cache_hit is False  # fresh fetch returned with cache_hit=False

    second = await cache_table.cache_or_fetch(key, fetch)
    assert invocation_count == 1  # second call hit the cache, no new fetch
    assert second.cache_hit is True
