"""AWS Lambda entrypoint for the nightly tenant cost rollup aggregator.

Triggered by an EventBridge Scheduler rule firing daily at 02:00 UTC
(see `infra/dev/cost-rollup-aggregator/`). 02:00 UTC is the lowest-
traffic window for the AWS billing API and gives CE 2 hours past the
UTC-day boundary to settle refreshed numbers before we read them.

The handler is intentionally thin:

  1. Resolve the target day (default: yesterday-UTC; explicit
     `event["day"]` honored as an override knob for replays).
  2. Construct a CE client + a `TenantRollupStore` against the
     production rollup table.
  3. Delegate to `aggregator.aggregate_day` and return its summary as
     the Lambda response so the EventBridge Scheduler invocation log
     captures the structured shape.

Errors propagate so Lambda's retry / DLQ behavior kicks in. Cost
Explorer throttling and transient `ClientError` are not specially
handled here; the EventBridge Scheduler is configured with no retries
because the aggregator is upserting and the next nightly run will
naturally re-aggregate any day that failed.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3

from panakoes_cost_rollup_aggregator.aggregator import aggregate_day

if TYPE_CHECKING:
    from mypy_boto3_ce.client import CostExplorerClient
    from mypy_boto3_dynamodb.service_resource import DynamoDBServiceResource


logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.INFO)


def _resolve_day(event: dict[str, Any]) -> date:
    """Return the target day from `event["day"]` if present, else UTC yesterday.

    Explicit override is the replay knob: an operator triggering the
    Lambda manually (or via a one-off EventBridge invocation) can pin a
    specific day. The default is yesterday in UTC because that is the
    most-recently-closed day at the 02:00 UTC fire time.
    """
    raw = event.get("day")
    if isinstance(raw, str) and raw:
        return date.fromisoformat(raw)
    today = datetime.now(UTC).date()
    return today - timedelta(days=1)


def _build_store(
    table_name: str,
    dynamodb_resource: DynamoDBServiceResource | None = None,
) -> Any:
    """Construct the `TenantRollupStore` against the production table.

    Imports happen inside the function so the module-level import graph
    stays small; this keeps the Lambda cold-start cheap and avoids
    pulling FastAPI / pydantic on the import path before we need them
    (we never need them; the store reuse is just for the DynamoDB
    primitive). Returns the store typed as `Any` because mypy strict
    cannot see across the editable cost-api install in this worktree.
    """
    from panakoes_cost_api.tenant_rollup import TenantRollupStore

    if dynamodb_resource is None:
        region = os.environ.get("AWS_REGION", "us-east-1")
        dynamodb_resource = boto3.resource("dynamodb", region_name=region)
    table = dynamodb_resource.Table(table_name)
    return TenantRollupStore(table=table)


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint.

    `context` is the standard `LambdaContext`; we do not consult it
    today but keep the parameter so the runtime contract matches.
    """
    del context  # unused; kept for runtime contract
    table_name = os.environ.get("TENANT_COST_ROLLUP_TABLE", "")
    if not table_name:
        raise RuntimeError("TENANT_COST_ROLLUP_TABLE env var is required")
    region = os.environ.get("AWS_REGION", "us-east-1")

    day = _resolve_day(event)
    store = _build_store(table_name)
    ce_client: CostExplorerClient = boto3.client("ce", region_name=region)

    summary = aggregate_day(ce_client, store, day)
    return summary.as_dict()
