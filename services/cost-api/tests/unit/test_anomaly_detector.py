"""Unit tests for `AnomalyDetector`.

Combines a mocked Cost Explorer client (the CE SDK is synchronous and
the detector wraps it via `asyncio.to_thread`) with a moto-backed
`AlertStateStore` so the dedup interactions exercise real DynamoDB
semantics. No real AWS calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from panakoes_cost_api.alert_state import AlertStateStore
from panakoes_cost_api.anomaly_detector import AnomalyDetector
from panakoes_cost_api.models import CostAnomaly, DateRange


def _ce_anomaly_dict(
    anomaly_id: str,
    actual: float,
    expected: float,
    impact_pct: float,
    *,
    monitor_arn: str = "arn:aws:ce::000000000000:anomalymonitor/abc",
    service: str = "Amazon Elastic Compute Cloud - Compute",
) -> dict[str, object]:
    """Build one CE GetAnomalies response entry for tests."""
    return {
        "AnomalyId": anomaly_id,
        "AnomalyStartDate": "2026-04-15",
        "AnomalyEndDate": "2026-04-16",
        "MonitorArn": monitor_arn,
        "Impact": {
            "TotalActualSpend": actual,
            "TotalExpectedSpend": expected,
            "TotalImpactPercentage": impact_pct,
        },
        "RootCauses": [{"Service": service}],
    }


@pytest.fixture
def alert_store() -> Iterator[AlertStateStore]:
    """A fresh moto-backed alert-state table per test."""
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
async def test_detect_from_cost_explorer_happy_path(alert_store: AlertStateStore) -> None:
    """A CE response with two anomalies maps to two `CostAnomaly` rows."""
    ce_mock = MagicMock()
    ce_mock.get_anomalies.return_value = {
        "Anomalies": [
            _ce_anomaly_dict("a-1", actual=200.00, expected=50.00, impact_pct=300.0),
            _ce_anomaly_dict("a-2", actual=180.00, expected=120.00, impact_pct=50.0),
        ],
    }
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_store)

    window = DateRange(from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    anomalies = await detector.detect_from_cost_explorer(window)

    assert len(anomalies) == 2
    a1 = next(a for a in anomalies if a.signature == "a-1")
    assert a1.observed_cost_cents == 20000
    assert a1.expected_cost_cents == 5000
    assert a1.deviation_pct == 300.0
    assert a1.suppressed is False
    assert "Amazon Elastic Compute Cloud" in a1.dimension_key


@pytest.mark.unit
async def test_detect_filters_by_min_deviation_threshold(
    alert_store: AlertStateStore,
) -> None:
    """An anomaly under the threshold is dropped from the result list."""
    ce_mock = MagicMock()
    ce_mock.get_anomalies.return_value = {
        "Anomalies": [
            # Above threshold (default 20%): kept.
            _ce_anomaly_dict("a-loud", actual=200.00, expected=100.00, impact_pct=100.0),
            # Below threshold: filtered.
            _ce_anomaly_dict("a-quiet", actual=110.00, expected=100.00, impact_pct=10.0),
        ],
    }
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_store)

    window = DateRange(from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    anomalies = await detector.detect_from_cost_explorer(window)

    signatures = {a.signature for a in anomalies}
    assert signatures == {"a-loud"}


@pytest.mark.unit
async def test_detect_no_monitor_returns_empty(alert_store: AlertStateStore) -> None:
    """A CE error consistent with no monitor configured returns `[]`, not a 500."""
    ce_mock = MagicMock()
    ce_mock.get_anomalies.side_effect = ClientError(
        error_response={
            "Error": {"Code": "ResourceNotFoundException", "Message": "no monitor"},
        },
        operation_name="GetAnomalies",
    )
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_store)

    window = DateRange(from_date=date(2026, 4, 1), to_date=date(2026, 5, 1))
    anomalies = await detector.detect_from_cost_explorer(window)

    assert anomalies == []


@pytest.mark.unit
def test_read_active_alerts_returns_active_only(alert_store: AlertStateStore) -> None:
    """`scan_active()` -> `read_active_alerts()` surfaces fresh rows; expired rows omitted."""
    fresh = CostAnomaly(
        signature="sig-fresh",
        detector="ce-monitor",
        tenant_id=None,
        dimension_key="Amazon EC2",
        observed_cost_cents=20000,
        expected_cost_cents=5000,
        deviation_pct=300.0,
        first_seen=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC),
        suppressed=False,
    )
    alert_store.put("sig-fresh", fresh)

    # Stale row injected directly so we can pin an expired epoch.
    stale = CostAnomaly(
        signature="sig-stale",
        detector="ce-monitor",
        tenant_id=None,
        dimension_key="Amazon S3",
        observed_cost_cents=1000,
        expected_cost_cents=500,
        deviation_pct=100.0,
        first_seen=datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 4, 2, 0, 0, 0, tzinfo=UTC),
        suppressed=False,
    )
    alert_store._table.put_item(  # test exercising stale-row contract
        Item={
            "alert_signature": "sig-stale",
            "expires_at": int(datetime.now(UTC).timestamp()) - 60,
            "payload": stale.model_dump_json(),
        }
    )

    ce_mock = MagicMock()
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_store)

    active = detector.read_active_alerts()
    signatures = {a.signature for a in active}
    assert signatures == {"sig-fresh"}


@pytest.mark.unit
def test_read_active_alerts_filters_by_detector(alert_store: AlertStateStore) -> None:
    """The detector filter narrows the result to one detector name."""
    base = CostAnomaly(
        signature="sig-a",
        detector="ce-monitor",
        tenant_id=None,
        dimension_key="EC2",
        observed_cost_cents=10000,
        expected_cost_cents=5000,
        deviation_pct=100.0,
        first_seen=datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC),
        last_seen=datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC),
        suppressed=False,
    )
    other = base.model_copy(update={"signature": "sig-b", "detector": "tenant-monitor"})
    alert_store.put("sig-a", base)
    alert_store.put("sig-b", other)

    ce_mock = MagicMock()
    detector = AnomalyDetector(ce_client=ce_mock, alert_state=alert_store)

    only_ce = detector.read_active_alerts(detector="ce-monitor")
    assert {a.signature for a in only_ce} == {"sig-a"}

    only_tenant = detector.read_active_alerts(detector="tenant-monitor")
    assert {a.signature for a in only_tenant} == {"sig-b"}
