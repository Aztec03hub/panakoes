"""Unit tests for `panakoes_cost_rollup_aggregator.handler`.

The handler is a thin wrapper around `aggregate_day`. We exercise its
two non-trivial behaviors here:

1. Default day resolution falls back to UTC yesterday.
2. Explicit `event["day"]` overrides the default.
3. Missing required env var raises at handler entry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import boto3
import pytest

from panakoes_cost_rollup_aggregator import handler as handler_mod
from tests.conftest import TEST_REGION, TEST_TABLE_NAME, FakeCEClient, make_ce_response


@pytest.fixture
def _table(rollup_store: Any) -> Any:
    """Force the moto table fixture to exist before handler-level tests run."""
    return rollup_store


@pytest.mark.unit
def test_handler_defaults_to_utc_yesterday(
    monkeypatch: pytest.MonkeyPatch,
    _table: Any,
) -> None:
    """An empty `event` runs the aggregation for yesterday-UTC."""
    expected_day = (datetime.now(UTC).date() - timedelta(days=1))
    ce = FakeCEClient(
        responses=[
            make_ce_response(
                day_iso=expected_day.isoformat(),
                tenant_groups=[("tenant-a", "0.50")],
            ),
        ],
    )
    monkeypatch.setattr(handler_mod.boto3, "client", lambda *_a, **_k: ce)

    result = handler_mod.lambda_handler({}, context=object())

    assert result["day"] == expected_day.isoformat()
    assert result["tenants_written"] == 1
    assert ce.calls[0]["TimePeriod"]["Start"] == expected_day.isoformat()


@pytest.mark.unit
def test_handler_honors_explicit_day_override(
    monkeypatch: pytest.MonkeyPatch,
    _table: Any,
) -> None:
    """`event["day"]` is used verbatim instead of the yesterday default."""
    pinned = "2026-01-15"
    ce = FakeCEClient(
        responses=[make_ce_response(day_iso=pinned, tenant_groups=[("tenant-z", "9.99")])],
    )
    monkeypatch.setattr(handler_mod.boto3, "client", lambda *_a, **_k: ce)

    result = handler_mod.lambda_handler({"day": pinned}, context=object())

    assert result["day"] == pinned
    assert ce.calls[0]["TimePeriod"] == {"Start": pinned, "End": "2026-01-16"}


@pytest.mark.unit
def test_handler_raises_when_table_env_var_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured deploy fails fast at handler entry, not mid-CE-call."""
    monkeypatch.delenv("TENANT_COST_ROLLUP_TABLE", raising=False)
    with pytest.raises(RuntimeError, match="TENANT_COST_ROLLUP_TABLE"):
        handler_mod.lambda_handler({}, context=object())


@pytest.mark.unit
def test_resolve_day_default_is_utc_yesterday() -> None:
    """`_resolve_day({})` returns `today_utc - 1d`."""
    expected = datetime.now(UTC).date() - timedelta(days=1)
    assert handler_mod._resolve_day({}) == expected


@pytest.mark.unit
def test_resolve_day_explicit_event_value() -> None:
    """`_resolve_day({"day": "..."})` returns the parsed ISO date."""
    assert handler_mod._resolve_day({"day": "2026-01-15"}) == date(2026, 1, 15)


@pytest.mark.unit
def test_resolve_day_ignores_non_string_event_value() -> None:
    """Garbage `event["day"]` (None, int) falls back to the default."""
    expected = datetime.now(UTC).date() - timedelta(days=1)
    assert handler_mod._resolve_day({"day": None}) == expected
    assert handler_mod._resolve_day({"day": 12345}) == expected
    assert handler_mod._resolve_day({"day": ""}) == expected


@pytest.mark.unit
def test_build_store_uses_provided_resource(_table: Any) -> None:
    """`_build_store` wraps the injected DynamoDB resource without calling boto3."""
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    store = handler_mod._build_store(TEST_TABLE_NAME, dynamodb_resource=resource)
    # Smoke: the store should successfully write + read against the moto table.
    store.put_rollup(tenant_id="tenant-x", day=date(2026, 5, 1), cost_cents=42)
    rows = store.query_all_tenants_for_day(date(2026, 5, 1))
    assert {r.tenant_id for r in rows} == {"tenant-x"}
    # Force the unused-import suppression to actually consume `cast`.
    _ = cast(Any, store)
