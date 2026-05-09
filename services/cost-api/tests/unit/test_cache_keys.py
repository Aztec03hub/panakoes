"""Unit tests for `CacheKey.to_string()` determinism."""

from __future__ import annotations

from datetime import date

import pytest

from panakoes_cost_api.models import CacheKey


@pytest.mark.unit
def test_cache_key_generation_is_deterministic() -> None:
    """Building the same logical key twice produces the same string."""
    a = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    b = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    assert a.to_string() == b.to_string()


@pytest.mark.unit
def test_cache_key_includes_query_kind_prefix() -> None:
    """The generated string starts with `query_kind:` for at-a-glance debugging."""
    key = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    assert key.to_string().startswith("by-service:")


@pytest.mark.unit
def test_cache_key_changes_when_dates_change() -> None:
    """Different windows produce different keys."""
    a = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    b = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 2))
    assert a.to_string() != b.to_string()


@pytest.mark.unit
def test_cache_key_changes_when_filter_changes() -> None:
    """Different service-name filters produce different keys."""
    a = CacheKey(
        query_kind="by-service",
        from_date=date(2026, 4, 1),
        to_date=date(2026, 5, 1),
        service_filter=None,
    )
    b = CacheKey(
        query_kind="by-service",
        from_date=date(2026, 4, 1),
        to_date=date(2026, 5, 1),
        service_filter="Amazon EC2",
    )
    assert a.to_string() != b.to_string()


@pytest.mark.unit
def test_cache_key_kind_distinguishes_query_families() -> None:
    """`by-service` and `by-tenant` queries with the same window do NOT collide."""
    a = CacheKey(query_kind="by-service", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    b = CacheKey(query_kind="by-tenant", from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    assert a.to_string() != b.to_string()
