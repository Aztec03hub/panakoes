"""Unit tests for the moto-backed `AlertStateStore`.

The store wraps `panakoes-dev-alert-state` (HK `alert_signature`, TTL
on `expires_at`) and is the dedup layer the anomaly detector reads.
Every test runs inside `mock_aws()` so DynamoDB calls hit the
in-process moto stub; no real AWS credentials are required.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from panakoes_cost_api.alert_state import (
    DEFAULT_QUIET_PERIOD_SECONDS,
    AlertStateStore,
)
from panakoes_cost_api.models import CostAnomaly


def _sample_anomaly(signature: str = "sig-1") -> CostAnomaly:
    """Build a deterministic CostAnomaly for tests."""
    return CostAnomaly(
        signature=signature,
        detector="ce-monitor",
        tenant_id=None,
        dimension_key="Amazon EC2",
        observed_cost_cents=10000,
        expected_cost_cents=4000,
        deviation_pct=150.0,
        first_seen=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC),
        suppressed=False,
    )


@pytest.fixture
def alert_store() -> Iterator[AlertStateStore]:
    """A fresh moto-backed alert-state table per test.

    Schema mirrors `infra/dev/admin-state/main.tf`'s `alert_state`
    table: `alert_signature` HK with the TTL attribute on
    `expires_at`. moto does not honor TTL deletion (matches DynamoDB's
    real ~48h sweep latency), so `scan_active()` does the filtering
    client-side, which the tests assert directly.
    """
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-alert-state",
            KeySchema=[{"AttributeName": "alert_signature", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "alert_signature", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield AlertStateStore(table=table)


@pytest.mark.unit
def test_get_miss_returns_none(alert_store: AlertStateStore) -> None:
    """An unknown signature returns None rather than raising."""
    assert alert_store.get("never-written") is None


@pytest.mark.unit
def test_put_then_get_roundtrips_anomaly_payload(
    alert_store: AlertStateStore,
) -> None:
    """The full anomaly payload roundtrips through put + get unchanged."""
    anomaly = _sample_anomaly("sig-roundtrip")
    alert_store.put("sig-roundtrip", anomaly)

    row = alert_store.get("sig-roundtrip")
    assert row is not None
    assert row.signature == "sig-roundtrip"
    assert row.anomaly == anomaly
    # `expires_at` is in the future given the default 24h quiet period.
    now_epoch = int(datetime.now(UTC).timestamp())
    assert row.expires_at > now_epoch
    assert row.expires_at <= now_epoch + DEFAULT_QUIET_PERIOD_SECONDS + 5


@pytest.mark.unit
def test_put_rejects_non_positive_ttl(alert_store: AlertStateStore) -> None:
    """`ttl_seconds <= 0` raises ValueError; the dedup contract requires a real TTL."""
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        alert_store.put("sig-bad-ttl", _sample_anomaly(), ttl_seconds=0)
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        alert_store.put("sig-bad-ttl-neg", _sample_anomaly(), ttl_seconds=-1)


@pytest.mark.unit
def test_scan_active_filters_expired_rows(alert_store: AlertStateStore) -> None:
    """`scan_active()` returns only rows whose `expires_at` is in the future.

    The fixture writes one row with a TTL well in the past (manually
    via `_table.put_item`) and one row with the default future TTL.
    Only the future row should come back.
    """
    # Fresh row with future expires_at (via the public path).
    alert_store.put("sig-fresh", _sample_anomaly("sig-fresh"))

    # Stale row with expires_at in the past. Bypass the public path
    # so we can hand-craft an already-expired epoch; this is what
    # DynamoDB will eventually clean up but hasn't yet.
    stale_anomaly = _sample_anomaly("sig-stale")
    alert_store._table.put_item(  # test exercising stale-row contract
        Item={
            "alert_signature": "sig-stale",
            "expires_at": int(datetime.now(UTC).timestamp()) - 60,
            "payload": stale_anomaly.model_dump_json(),
        }
    )

    active = alert_store.scan_active()
    signatures = {row.signature for row in active}
    assert signatures == {"sig-fresh"}
