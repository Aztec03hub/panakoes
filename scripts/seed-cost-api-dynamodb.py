#!/usr/bin/env python3
"""Seed DynamoDB tables with realistic synthetic data for the cost-api dashboard.

This is a one-off operator script for the Panakoes dev environment. Its
job is to make the admin dashboard's cost pages (`/cost/by-service`,
`/cost/by-tenant`, `/cost/forecast`, `/cost/anomalies`) render rich
realistic data BEFORE the nightly cost-rollup-aggregator and the AWS
Cost Anomaly Monitor have produced any real signal. Without seed data
those pages render empty even when the SPA auth flow lands, which
muddies the end-to-end sign-in smoke test.

Targets (all in account 659225405128, region us-east-1):
  - panakoes-dev-cost-cache: pre-rendered CostBreakdown + TenantCostBreakdown
    JSON envelopes for the 7 / 14 / 30 day windows ending today UTC.
    The cost-api route layer (`routes/cost.py`) computes a CacheKey of
    shape `<query_kind>:<sha256(...)>` and reads `payload` (JSON of the
    response model) plus `expires_at` (TTL). We pre-populate those exact
    cache_keys so the route serves seed data without a Cost Explorer
    round trip.
  - panakoes-dev-tenant-cost-rollup: one row per (tenant_id, day) for
    the last 30 days, schema `(tenant_id S HK, day S RK, cost_cents N)`.
  - panakoes-dev-alert-state: two active anomaly rows with TTL ~24h.
    Schema `(alert_signature S HK, expires_at N TTL, payload S JSON)`.
  - panakoes-dev-audit-log: skipped. The admin-api owns its writes and
    it is not consumed by any of the four cost dashboard pages.

Safety:
  - Refuses to run unless the active AWS profile's account is 659225405128,
    unless --force is set. This stops an accidental prod seed.
  - --dry-run prints exactly what would be written without any boto3
    write calls.
  - --limit N reduces the per-table item count for fast smoke iteration.

Idempotency:
  - All writes go through `BatchWriteItem` with PutRequests; re-running
    the script overwrites prior items keyed by their PKs. Safe to re-run.

Cleanup:
  See `scripts/README.md` for the documented teardown snippet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

# boto3 / botocore are optional only because the unit-test imports the
# module under a stripped-down environment. Inside the operator workflow
# they are always present via the cost-api service's uv env.
_boto3: Any
try:
    import boto3 as _boto3_real
    from botocore.exceptions import ClientError

    _boto3 = _boto3_real
except ImportError:  # pragma: no cover - operator-time path only
    _boto3 = None
    ClientError = Exception  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEV_ACCOUNT_ID = "659225405128"
DEFAULT_REGION = "us-east-1"

COST_CACHE_TABLE = "panakoes-dev-cost-cache"
TENANT_ROLLUP_TABLE = "panakoes-dev-tenant-cost-rollup"
ALERT_STATE_TABLE = "panakoes-dev-alert-state"

# Windows the dashboard's date selector hits most often; we pre-render
# a cache entry for each so the SPA hits a warm cache regardless of
# which preset the user picks.
DEFAULT_WINDOWS_DAYS: tuple[int, ...] = (7, 14, 30)

# Cache TTL on the seed entries. Long enough that the demo dashboard
# stays warm across a multi-day stretch of E2E tests; short enough that
# the rows naturally age out and a future real CE response replaces them.
CACHE_TTL_SECONDS = 7 * 24 * 3600

# Active-alert TTL for the anomaly seed rows. Mirrors the cost-api
# `DEFAULT_QUIET_PERIOD_SECONDS` (24h) so the dashboard treats them as
# fresh signal until the next operator run.
ALERT_TTL_SECONDS = 24 * 3600

# Deterministic seed so re-runs produce byte-identical synthetic data
# until somebody flips the date. Critical for review-friendly diffs and
# for tests that pin specific shapes.
RANDOM_SEED = 20260511

# Synthetic but realistic AWS-service mix. The values are the canonical
# names Cost Explorer returns under the `SERVICE` dimension; pulling
# from this list keeps the seed legible to anyone who has seen a real
# Cost Explorer breakdown.
AWS_SERVICES: tuple[tuple[str, float], ...] = (
    # (display name, baseline daily cost in cents)
    ("Amazon Bedrock", 4_200),
    ("Amazon EC2", 3_800),
    ("Amazon S3", 1_650),
    ("AWS Lambda", 950),
    ("Amazon DynamoDB", 720),
    ("Amazon CloudFront", 510),
    ("Amazon ECS", 1_180),
    ("Amazon RDS", 2_240),
)

# Synthetic tenants. Names are deliberately constructed (no real PII
# and no real customer names). The dollar figures are rough monthly
# spends in cents that the per-day generator will spread across 30 days
# with a per-tenant service-mix bias.
TENANTS: tuple[tuple[str, str, int], ...] = (
    # (tenant_id, display_name, monthly_spend_cents)
    ("tenant-acme", "Acme Audio Labs", 4_200),
    ("tenant-zenith", "Zenith Studios", 8_700),
    ("tenant-orbit", "Orbit Transcripts Co", 15_600),
    ("tenant-nimbus", "Nimbus Media Group", 32_000),
    ("tenant-vega", "Vega Voice Platform", 184_000),
)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per record, suitable for CloudWatch ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName", "taskName",
            }:
                continue
            payload[key] = value
        return json.dumps(payload, separators=(",", ":"), default=str)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("seed_cost_api")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


log = _build_logger()


def _emit(level: int, event: str, **fields: Any) -> None:
    """Wrapper that funnels structured fields through stdlib `extra=`.

    stdlib `Logger.info(...)` does not accept arbitrary keyword arguments
    the way `structlog` does. Using `extra={...}` is the canonical way
    to attach custom attributes to a `LogRecord`; the JSON formatter
    above pulls them off `record.__dict__`. Centralizing here keeps every
    log call type-clean for mypy and consistent for CloudWatch.
    """
    log.log(level, event, extra=fields)


# ---------------------------------------------------------------------------
# Data generation: pure functions, no AWS calls. Unit-testable.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyServiceCost:
    """One day of synthetic cost for one AWS service."""

    day: date
    service: str
    cost_cents: int


def _cache_key_string(query_kind: str, from_date: date, to_date: date) -> str:
    """Mirror `models.CacheKey.to_string()` so the seed lands at the exact
    cache_key the route looks up.

    Kept in lockstep with `services/cost-api/src/panakoes_cost_api/models.py`.
    If that function changes shape, this one must change with it; the
    test suite asserts they agree.
    """
    canonical = json.dumps(
        {
            "kind": query_kind,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "group_by": None,
            "service_filter": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{query_kind}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def generate_daily_service_costs(
    today: date,
    days: int = 30,
    rng: random.Random | None = None,
) -> list[DailyServiceCost]:
    """Produce `days` of synthetic per-service costs ending `today` (exclusive).

    Properties of the generated series:
      - Slight upward trend (~0.7% / day) so the dashboard's stacked area
        chart shows visible growth across the window.
      - Weekly seasonality: weekends drop to ~70% of baseline.
      - Two anomalous spikes: Bedrock at day-3 from today (3x trailing
        median) and DynamoDB at day-1 (2.5x). These are the spikes the
        anomaly detector page surfaces.
    """
    rng = rng or random.Random(RANDOM_SEED)  # noqa: S311 - synthetic seed data, not crypto
    rows: list[DailyServiceCost] = []
    for delta in range(days, 0, -1):
        day = today - timedelta(days=delta)
        # Trend factor: oldest day is at baseline, newest day is ~+21%.
        trend = 1.0 + (days - delta) * 0.007
        # Weekend dip: Saturday=5, Sunday=6.
        seasonality = 0.70 if day.weekday() >= 5 else 1.00
        for service, baseline in AWS_SERVICES:
            jitter = rng.uniform(0.85, 1.15)
            cost = baseline * trend * seasonality * jitter
            # Anomalies: realistic-looking spikes the detector will flag.
            if service == "Amazon Bedrock" and delta == 3:
                cost *= 3.0
            elif service == "Amazon DynamoDB" and delta == 1:
                cost *= 2.5
            rows.append(
                DailyServiceCost(
                    day=day,
                    service=service,
                    cost_cents=max(0, round(cost)),
                )
            )
    return rows


def aggregate_by_service(
    rows: Iterable[DailyServiceCost],
    from_date: date,
    to_date: date,
) -> dict[str, int]:
    """Sum cost_cents per service across `[from_date, to_date)`."""
    out: dict[str, int] = {}
    for row in rows:
        if from_date <= row.day < to_date:
            out[row.service] = out.get(row.service, 0) + row.cost_cents
    return out


def build_cost_breakdown_payload(
    from_date: date,
    to_date: date,
    rows: Iterable[DailyServiceCost],
) -> dict[str, Any]:
    """Build the JSON payload the cost-api cache returns for /by-service.

    Shape mirrors `CostBreakdown` in
    `services/cost-api/src/panakoes_cost_api/models.py`.
    """
    by_service = aggregate_by_service(rows, from_date, to_date)
    total = sum(by_service.values())
    services = []
    for service, cents in sorted(by_service.items(), key=lambda kv: kv[1], reverse=True):
        pct = round((cents / total * 100.0), 2) if total > 0 else 0.0
        services.append(
            {
                "service": service,
                "cost_cents": cents,
                "percent_of_total": pct,
            }
        )
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "currency": "USD",
        "services": services,
        "total_cents": total,
        "cache_hit": False,
        "queried_at": datetime.now(UTC).isoformat(),
    }


def generate_tenant_daily_rows(
    today: date,
    days: int = 30,
    rng: random.Random | None = None,
) -> list[tuple[str, date, int]]:
    """Produce daily (tenant_id, day, cost_cents) rows for the rollup table.

    Each tenant's daily cents drifts around `monthly_spend_cents / days`
    with weekend seasonality and jitter. The values per tenant are
    statistically independent of the per-service stream above, which
    matches the production aggregator: tenant rollups come from the
    audit-log + ingestion volume per tenant, not from a service split.
    """
    rng = rng or random.Random(RANDOM_SEED + 1)  # noqa: S311 - synthetic seed data, not crypto
    rows: list[tuple[str, date, int]] = []
    for tenant_id, _, monthly_cents in TENANTS:
        baseline = monthly_cents / max(1, days)
        for delta in range(days, 0, -1):
            day = today - timedelta(days=delta)
            seasonality = 0.65 if day.weekday() >= 5 else 1.05
            jitter = rng.uniform(0.80, 1.25)
            cost = baseline * seasonality * jitter
            rows.append((tenant_id, day, max(0, round(cost))))
    return rows


def build_tenant_breakdown_payload(
    from_date: date,
    to_date: date,
    daily_rows: Iterable[tuple[str, date, int]],
) -> dict[str, Any]:
    """Build the cached JSON payload for /by-tenant.

    Shape mirrors `TenantCostBreakdown` in cost-api models.
    """
    display_name_by_id = {tid: name for tid, name, _ in TENANTS}
    per_tenant: dict[str, int] = {}
    for tenant_id, day, cents in daily_rows:
        if from_date <= day < to_date:
            per_tenant[tenant_id] = per_tenant.get(tenant_id, 0) + cents
    total = sum(per_tenant.values())
    tenants = []
    for tenant_id, cents in sorted(per_tenant.items(), key=lambda kv: kv[1], reverse=True):
        pct = round((cents / total * 100.0), 2) if total > 0 else 0.0
        tenants.append(
            {
                "tenant_id": tenant_id,
                "display_name": display_name_by_id.get(tenant_id, tenant_id),
                "cost_cents": cents,
                "percent_of_total": pct,
            }
        )
    return {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "currency": "USD",
        "tenants": tenants,
        "total_cents": total,
        "cache_hit": False,
        "queried_at": datetime.now(UTC).isoformat(),
    }


def build_anomaly_payloads(today: date) -> list[dict[str, Any]]:
    """Two synthetic CostAnomaly payloads ready for the alert-state table.

    Mirrors `CostAnomaly` in cost-api models. `signature` is a stable
    hash of (detector, dimension_key, day) so re-running this script
    overwrites the same row rather than fanning out duplicates.
    """
    spike_day = today - timedelta(days=3)
    rate_day = today - timedelta(days=1)

    def _sig(detector: str, dimension: str, day: date) -> str:
        h = hashlib.sha256(f"{detector}|{dimension}|{day.isoformat()}".encode())
        return h.hexdigest()[:24]

    bedrock = {
        "signature": _sig("ce-cost-spike", "Amazon Bedrock", spike_day),
        "detector": "ce-cost-spike",
        "tenant_id": None,
        "dimension_key": "Amazon Bedrock",
        "observed_cost_cents": 12_600,
        "expected_cost_cents": 4_200,
        "deviation_pct": 200.0,
        "first_seen": datetime.combine(spike_day, datetime.min.time(), tzinfo=UTC).isoformat(),
        "last_seen": datetime.combine(spike_day, datetime.min.time(), tzinfo=UTC).isoformat(),
        "suppressed": False,
    }
    ddb = {
        "signature": _sig("ce-rate-of-change", "Amazon DynamoDB", rate_day),
        "detector": "ce-rate-of-change",
        "tenant_id": None,
        "dimension_key": "Amazon DynamoDB",
        "observed_cost_cents": 1_800,
        "expected_cost_cents": 720,
        "deviation_pct": 150.0,
        "first_seen": datetime.combine(rate_day, datetime.min.time(), tzinfo=UTC).isoformat(),
        "last_seen": datetime.combine(rate_day, datetime.min.time(), tzinfo=UTC).isoformat(),
        "suppressed": False,
    }
    return [bedrock, ddb]


# ---------------------------------------------------------------------------
# DynamoDB I/O
# ---------------------------------------------------------------------------


def _verify_account(session: Any, force: bool) -> str:
    """Return the active AWS account id, refusing non-dev unless --force.

    Why this exists: a misconfigured AWS_PROFILE can silently point at
    a production account and the script would happily seed live tables.
    `sts:GetCallerIdentity` is cheap, side-effect-free, and the canonical
    safety check for any operator script.
    """
    sts = session.client("sts")
    ident = sts.get_caller_identity()
    account = ident["Account"]
    if account != DEV_ACCOUNT_ID and not force:
        raise SystemExit(
            f"Refusing to seed account {account}; expected {DEV_ACCOUNT_ID}. "
            f"Pass --force if you are absolutely sure."
        )
    return str(account)


def _batch_put(table: Any, items: Sequence[dict[str, Any]]) -> int:
    """Write items via batch_writer; returns the count written."""
    n = 0
    with table.batch_writer(overwrite_by_pkeys=None) as batch:
        for item in items:
            batch.put_item(Item=item)
            n += 1
    return n


def _to_ddb_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a JSON-ready dict into something DynamoDB accepts.

    DynamoDB cannot store native floats; we convert via Decimal. The
    cost-api itself stores JSON-as-strings, so this is only needed for
    the integer-typed N attributes we write (cost_cents in the rollup
    table and expires_at on the cache + alert tables).
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, float):
            # DynamoDB N: use Decimal-of-str to avoid float binary drift.
            out[k] = Decimal(str(v))
        else:
            out[k] = v
    return out


def write_cost_cache(
    table: Any,
    today: date,
    windows: Sequence[int],
    daily_service_rows: Sequence[DailyServiceCost],
    tenant_daily_rows: Sequence[tuple[str, date, int]],
    expires_at: int,
    dry_run: bool,
    limit: int | None,
) -> int:
    """Write pre-rendered CostBreakdown + TenantCostBreakdown cache entries."""
    items: list[dict[str, Any]] = []
    for w in windows:
        from_date = today - timedelta(days=w)
        to_date = today
        # by-service
        svc_payload = build_cost_breakdown_payload(from_date, to_date, daily_service_rows)
        items.append(
            {
                "cache_key": _cache_key_string("by-service", from_date, to_date),
                "payload": json.dumps(svc_payload, separators=(",", ":")),
                "expires_at": expires_at,
            }
        )
        # by-tenant
        ten_payload = build_tenant_breakdown_payload(from_date, to_date, tenant_daily_rows)
        items.append(
            {
                "cache_key": _cache_key_string("by-tenant", from_date, to_date),
                "payload": json.dumps(ten_payload, separators=(",", ":")),
                "expires_at": expires_at,
            }
        )
    if limit is not None:
        items = items[:limit]
    if dry_run:
        _emit(logging.INFO, "cost_cache_dry_run", count=len(items),
                 sample_keys=[i["cache_key"] for i in items[:3]])
        return len(items)
    return _batch_put(table, items)


def write_tenant_rollup(
    table: Any,
    daily_rows: Sequence[tuple[str, date, int]],
    dry_run: bool,
    limit: int | None,
) -> int:
    items: list[dict[str, Any]] = [
        {
            "tenant_id": tid,
            "day": day.isoformat(),
            "cost_cents": cents,
        }
        for tid, day, cents in daily_rows
    ]
    if limit is not None:
        items = items[:limit]
    if dry_run:
        _emit(logging.INFO, "tenant_rollup_dry_run", count=len(items),
                 sample=items[:2])
        return len(items)
    return _batch_put(table, items)


def write_alert_state(
    table: Any,
    anomalies: Sequence[dict[str, Any]],
    expires_at: int,
    dry_run: bool,
    limit: int | None,
) -> int:
    items: list[dict[str, Any]] = [
        {
            "alert_signature": a["signature"],
            "expires_at": expires_at,
            "payload": json.dumps(a, separators=(",", ":")),
        }
        for a in anomalies
    ]
    if limit is not None:
        items = items[:limit]
    if dry_run:
        _emit(logging.INFO, "alert_state_dry_run", count=len(items),
                 sample_signatures=[i["alert_signature"] for i in items])
        return len(items)
    return _batch_put(table, items)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the items that would be written without calling boto3 writes.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass the dev-account check. Required if STS reports an account other "
            f"than {DEV_ACCOUNT_ID}."
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap each table's write count for fast smoke iteration.",
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days of historical data to generate (default 30).",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        _emit(logging.ERROR, "invalid_limit", limit=args.limit)
        return 2

    if _boto3 is None:
        _emit(logging.ERROR, "boto3_not_installed")
        return 2

    region = os.environ.get("AWS_REGION", DEFAULT_REGION)
    profile = os.environ.get("AWS_PROFILE")
    _emit(logging.INFO, "seed_start", region=region, profile=profile, dry_run=args.dry_run,
             days=args.days, limit=args.limit)

    session = _boto3.session.Session(region_name=region, profile_name=profile)
    if not args.dry_run:
        account = _verify_account(session, force=args.force)
        _emit(logging.INFO, "account_verified", account=account)
    else:
        _emit(logging.INFO, "dry_run_skipping_account_check")

    today = datetime.now(UTC).date()
    expires_at = int(datetime.now(UTC).timestamp()) + CACHE_TTL_SECONDS
    alert_expires_at = int(datetime.now(UTC).timestamp()) + ALERT_TTL_SECONDS

    daily_service_rows = generate_daily_service_costs(today, days=args.days)
    tenant_daily_rows = generate_tenant_daily_rows(today, days=args.days)
    anomalies = build_anomaly_payloads(today)

    ddb = session.resource("dynamodb")
    cache_table = ddb.Table(COST_CACHE_TABLE) if not args.dry_run else None
    rollup_table = ddb.Table(TENANT_ROLLUP_TABLE) if not args.dry_run else None
    alert_table = ddb.Table(ALERT_STATE_TABLE) if not args.dry_run else None

    summary: dict[str, int] = {}
    try:
        summary[COST_CACHE_TABLE] = write_cost_cache(
            cache_table, today, DEFAULT_WINDOWS_DAYS,
            daily_service_rows, tenant_daily_rows,
            expires_at=expires_at, dry_run=args.dry_run, limit=args.limit,
        )
        summary[TENANT_ROLLUP_TABLE] = write_tenant_rollup(
            rollup_table, tenant_daily_rows,
            dry_run=args.dry_run, limit=args.limit,
        )
        summary[ALERT_STATE_TABLE] = write_alert_state(
            alert_table, anomalies, expires_at=alert_expires_at,
            dry_run=args.dry_run, limit=args.limit,
        )
    except ClientError as exc:
        _emit(logging.ERROR, "ddb_write_failed", error=str(exc))
        return 1

    _emit(logging.INFO, "seed_complete", summary=summary)
    # Human-readable summary line for operator eyeballs.
    print()  # noqa: T201  - intentional, separates JSON log from summary block.
    print("Seed summary:")  # noqa: T201
    for tbl, n in summary.items():
        print(f"  {tbl}: {n} items")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
