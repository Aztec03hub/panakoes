"""DynamoDB-backed store for the cost-anomaly dedup table.

Wraps `panakoes-dev-alert-state` (HK `alert_signature`, TTL on
`expires_at`) with three operations:

- `get(signature)` returns one row by signature, or None if absent.
- `put(signature, anomaly, ttl_seconds)` writes a row with a TTL,
  serializing the anomaly's payload for the read path.
- `scan_active()` returns every non-expired row.

Why a Scan rather than a Query: the table's only access pattern beyond
the per-signature Get is "list every active alert for the dashboard".
The table is small by construction (one row per active dedup signature,
capped by each detector's quiet-period quota), so a full Scan is well
under DynamoDB's 1MB page budget for the lifetime of v0.1. If the
active-row count grows past a few hundred we can revisit (a GSI keyed
on a constant `is_active` partition + `expires_at` sort would let the
dashboard read by Query, but it costs nothing to defer that until the
operational signal demands it).

Storage contract (committed by `infra/dev/admin-state/main.tf`):

    pk: alert_signature (S)
    expires_at: N (Unix epoch seconds; DynamoDB TTL attribute)
    payload: S (JSON-serialized CostAnomaly model)

DynamoDB TTL deletion is asynchronous (within ~48h of the timestamp),
so `scan_active()` filters `expires_at > now()` client-side rather than
trusting the table to be physically clean. That keeps the "active"
semantics tight regardless of how recently the TTL sweeper ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from panakoes_cost_api.models import CostAnomaly, utcnow

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


logger = structlog.get_logger(__name__)


DEFAULT_QUIET_PERIOD_SECONDS = 86_400  # 24 hours; matches the Terraform comment.


@dataclass(frozen=True)
class AlertStateRow:
    """One stored alert-state row, hydrated back into a typed shape.

    Frozen dataclass rather than a Pydantic model because this is the
    storage-layer shape, not the wire-layer shape. The route layer
    converts these into `CostAnomaly` instances when assembling the
    `CostAnomalyList` response.
    """

    signature: str
    expires_at: int
    anomaly: CostAnomaly


class AlertStateStore:
    """DynamoDB-backed reader / writer for the alert-state table.

    Same injection pattern as `CostCache` and `TenantRollupStore`: the
    `Table` resource is handed in by the lifespan in production and by
    moto-backed fixtures in tests, which keeps the store under test
    without monkey-patching boto3.
    """

    def __init__(self, table: Table) -> None:
        self._table = table

    def get(self, signature: str) -> AlertStateRow | None:
        """Return the row for `signature`, or None if absent or corrupt."""
        response = self._table.get_item(Key={"alert_signature": signature})
        item = response.get("Item")
        if item is None:
            logger.debug("alert_state_miss", signature=signature)
            return None
        return _row_from_item(item)

    def put(
        self,
        signature: str,
        anomaly: CostAnomaly,
        ttl_seconds: int = DEFAULT_QUIET_PERIOD_SECONDS,
    ) -> None:
        """Persist an anomaly under `signature` with a TTL of `ttl_seconds`.

        The TTL drives DynamoDB's async deletion sweep AND the
        client-side `scan_active()` filter. A non-positive `ttl_seconds`
        is rejected because the dedup contract requires every row to
        have a real expiration; "active forever" is not a valid mode.
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        expires_at = int(utcnow().timestamp()) + ttl_seconds
        self._table.put_item(
            Item={
                "alert_signature": signature,
                "expires_at": expires_at,
                "payload": anomaly.model_dump_json(),
            }
        )
        logger.debug(
            "alert_state_put",
            signature=signature,
            ttl_seconds=ttl_seconds,
            expires_at=expires_at,
        )

    def scan_active(self) -> list[AlertStateRow]:
        """Return every row whose `expires_at` is in the future.

        The full-table scan paginates explicitly so a small table that
        outgrows one page (1MB at PAY_PER_REQUEST) still surfaces every
        active row. Filtering happens client-side because DynamoDB's
        FilterExpression evaluates AFTER the page is read, so the cost
        is the same either way and client-side keeps the type narrowing
        in one place.
        """
        now_epoch = int(utcnow().timestamp())
        rows: list[AlertStateRow] = []
        items: list[dict[str, Any]] = []
        last_key: dict[str, Any] | None = None
        while True:
            kwargs: dict[str, Any] = {}
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key
            response = self._table.scan(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                break

        for item in items:
            row = _row_from_item(item)
            if row is None:
                continue
            if row.expires_at > now_epoch:
                rows.append(row)
        return rows


def _row_from_item(item: dict[str, Any]) -> AlertStateRow | None:
    """Hydrate a DynamoDB Item into an `AlertStateRow`, or None if corrupt.

    A corrupt row (missing payload, unparseable JSON) is logged and
    skipped rather than raised; one bad row should not poison the
    whole `scan_active()` page. The TTL sweep will eventually clear it.
    """
    signature = item.get("alert_signature")
    payload = item.get("payload")
    expires_at_raw = item.get("expires_at")
    if signature is None or payload is None or expires_at_raw is None:
        logger.warning("alert_state_corrupt_row", item_keys=list(item.keys()))
        return None
    if not isinstance(payload, str):
        logger.warning("alert_state_payload_not_string", signature=str(signature))
        return None
    try:
        anomaly = CostAnomaly.model_validate_json(payload)
    except ValueError as exc:
        logger.warning(
            "alert_state_payload_invalid",
            signature=str(signature),
            error=str(exc),
        )
        return None
    return AlertStateRow(
        signature=str(signature),
        expires_at=int(expires_at_raw),
        anomaly=anomaly,
    )
