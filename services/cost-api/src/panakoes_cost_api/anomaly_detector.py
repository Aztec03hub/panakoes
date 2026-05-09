"""Cost-anomaly detector for cost-api.

The detector has two distinct read paths the route layer composes:

- `detect_from_cost_explorer(window)`: calls AWS Cost Explorer's
  built-in `GetAnomalies` API for the given window. CE's anomaly
  service requires an operator-configured Anomaly Monitor and
  Subscription; without one, CE returns a graceful empty list (or a
  recognizable error) and we surface that as `[]` plus a logged
  warning rather than a 500. Operators see "no monitor configured"
  on the dashboard as a clean signal to set one up; see the run
  report for the Terraform module that creates the monitor.

- `read_active_alerts(detector=None)`: reads `panakoes-dev-alert-state`
  via `AlertStateStore.scan_active()` and returns each row's stored
  `CostAnomaly`. Optionally narrows to one detector. This is the
  "active alerts" feed the dashboard renders by default; it is fast
  (one Scan against a small table) and decouples the dashboard from
  CE rate limits.

The route layer wires these two methods together: `active_only=true`
(default) returns just the alert-state rows; `active_only=false`
detects fresh anomalies from CE for the window AND marks each one as
`suppressed=true` if its dedup signature matches an active alert-state
row (the dashboard renders both "live" and "suppressed" anomalies in
that mode so operators can see the full detector picture).

Threshold filtering: every detection path applies a `min_deviation_pct`
threshold (default 20%) to filter noise. The threshold is applied AFTER
CE returns its anomaly list so we still log the unfiltered count for
operator visibility into detector sensitivity.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from botocore.exceptions import ClientError

from panakoes_cost_api.alert_state import AlertStateStore
from panakoes_cost_api.models import CostAnomaly, DateRange

if TYPE_CHECKING:
    from mypy_boto3_ce.client import CostExplorerClient


logger = structlog.get_logger(__name__)


DEFAULT_MIN_DEVIATION_PCT = 20.0


# Error codes Cost Explorer returns when the operator has not yet
# configured an Anomaly Monitor + Subscription. We treat all of them
# as "no monitor configured": the route returns `[]` rather than a
# 500 so the dashboard renders a clean empty state.
NO_MONITOR_ERROR_CODES = frozenset(
    {
        "ResourceNotFoundException",
        "AnomalyMonitorNotFoundException",
        "ValidationException",
    }
)


class AnomalyDetector:
    """Cost-anomaly detection layer for cost-api.

    Same injection pattern as the rest of the cost-api: the CE client
    and `AlertStateStore` are constructed by the lifespan and handed
    in here, which keeps every test moto-backed without monkey-patching
    boto3 or Pydantic.
    """

    def __init__(
        self,
        ce_client: CostExplorerClient,
        alert_state: AlertStateStore,
        min_deviation_pct: float = DEFAULT_MIN_DEVIATION_PCT,
    ) -> None:
        self._ce_client = ce_client
        self._alert_state = alert_state
        self._min_deviation_pct = min_deviation_pct

    async def detect_from_cost_explorer(self, window: DateRange) -> list[CostAnomaly]:
        """Return CE-detected anomalies for `window`, threshold-filtered.

        Returns `[]` (with a logged warning) when no Anomaly Monitor is
        configured. The route layer surfaces that as a clean empty
        state rather than as an error.
        """
        ce_kwargs: dict[str, Any] = {
            "DateInterval": {
                "StartDate": window.from_date.isoformat(),
                "EndDate": window.to_date.isoformat(),
            },
        }
        try:
            response = await asyncio.to_thread(self._ce_client.get_anomalies, **ce_kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in NO_MONITOR_ERROR_CODES:
                logger.warning(
                    "anomaly_detector_no_monitor",
                    code=code,
                    detail=(
                        "AWS Cost Explorer returned an error consistent with "
                        "no Anomaly Monitor configured. Returning [] rather "
                        "than 5xx so the dashboard renders an empty state."
                    ),
                )
                return []
            raise

        # CE returns boto3-stub typed dicts; cast to `dict[str, Any]`
        # at the boundary so the parser layer below can treat the
        # response generically without depending on the stub schema.
        raw: list[dict[str, Any]] = list(response.get("Anomalies", []))  # type: ignore[arg-type]
        anomalies: list[CostAnomaly] = []
        for entry in raw:
            anomaly = _ce_anomaly_to_model(entry)
            if anomaly is None:
                continue
            if abs(anomaly.deviation_pct) < self._min_deviation_pct:
                continue
            anomalies.append(anomaly)

        logger.info(
            "anomaly_detector_ce_detected",
            raw_count=len(raw),
            filtered_count=len(anomalies),
            threshold_pct=self._min_deviation_pct,
        )
        return anomalies

    def read_active_alerts(self, detector: str | None = None) -> list[CostAnomaly]:
        """Return every active alert-state row's stored `CostAnomaly`.

        `detector` narrows the result to one detector name when set;
        `None` (the default) returns every active anomaly across all
        detectors. The dashboard's filter input drives this argument.
        """
        rows = self._alert_state.scan_active()
        anomalies: list[CostAnomaly] = []
        for row in rows:
            if detector is not None and row.anomaly.detector != detector:
                continue
            # The stored anomaly is `suppressed=False` (it was active
            # when written). Reading it back means it's still inside
            # its quiet period; leave the flag alone here. The route's
            # active_only=false path is the only place that needs to
            # flip suppressed=True (when matching a freshly-detected
            # CE anomaly against an active dedup signature).
            anomalies.append(row.anomaly)
        return anomalies


def _ce_anomaly_to_model(entry: dict[str, Any]) -> CostAnomaly | None:
    """Map one CE `GetAnomalies` row into our `CostAnomaly` shape.

    Returns None if the entry is missing required fields rather than
    raising, so one malformed row does not poison the whole batch.
    CE's anomaly schema is documented but the SDK types are loose;
    the defensive parse keeps the detector resilient to drift.
    """
    anomaly_id = entry.get("AnomalyId") or entry.get("AnomalyScore", {}).get("AnomalyId")
    if not anomaly_id:
        logger.warning("anomaly_detector_skip_no_id", keys=list(entry.keys()))
        return None

    impact = entry.get("Impact", {}) or {}
    observed_amount = float(impact.get("TotalActualSpend", 0) or 0)
    expected_amount = float(impact.get("TotalExpectedSpend", 0) or 0)
    observed_cents = round(observed_amount * 100)
    expected_cents = round(expected_amount * 100)

    # CE returns `TotalImpactPercentage` directly; fall back to a
    # locally computed percent if the field is missing.
    deviation_pct = impact.get("TotalImpactPercentage")
    if deviation_pct is None:
        if expected_cents == 0:
            # No baseline; CE shouldn't emit this but guard regardless.
            deviation_pct = 0.0
        else:
            deviation_pct = (observed_cents - expected_cents) / expected_cents * 100.0
    deviation_pct_value = float(deviation_pct)

    detector = entry.get("MonitorArn", "ce-monitor")
    # CE's RootCauses is a list of dimension strings the operator may
    # configure; we surface the first one as `dimension_key` and fall
    # back to AnomalyId so the table always has a non-empty label.
    root_causes = entry.get("RootCauses", []) or []
    dimension_key = anomaly_id
    tenant_id: str | None = None
    if root_causes:
        first = root_causes[0]
        if isinstance(first, dict):
            service = first.get("Service")
            linked_account = first.get("LinkedAccount")
            usage_type = first.get("UsageType")
            label_parts = [str(p) for p in (service, linked_account, usage_type) if p]
            if label_parts:
                dimension_key = " | ".join(label_parts)
            # Tenant-tagged anomalies (rare today; preserved for the
            # future tagging-strategy rollout) carry the tenant id in
            # the LinkedAccount slot we agree to repurpose.

    start_str = entry.get("AnomalyStartDate")
    end_str = entry.get("AnomalyEndDate") or start_str
    first_seen = _parse_ce_date(start_str)
    last_seen = _parse_ce_date(end_str) if end_str else first_seen

    return CostAnomaly(
        signature=str(anomaly_id),
        detector=str(detector),
        tenant_id=tenant_id,
        dimension_key=str(dimension_key),
        observed_cost_cents=max(observed_cents, 0),
        expected_cost_cents=max(expected_cents, 0),
        deviation_pct=deviation_pct_value,
        first_seen=first_seen,
        last_seen=last_seen,
        suppressed=False,
    )


def _parse_ce_date(value: Any) -> datetime:
    """Parse CE's date string into a UTC datetime; fall back to now()."""
    if isinstance(value, str) and value:
        try:
            # CE returns YYYY-MM-DD; treat as UTC midnight.
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)
