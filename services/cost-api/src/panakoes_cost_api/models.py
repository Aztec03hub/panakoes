"""Domain models for the cost-api service.

These shapes are the contract between the FastAPI route layer (Phase 1.3),
the Cost Explorer client (Phase 1.1-1.2), the DynamoDB cache (also
Phase 1.1-1.2), and the SvelteKit frontend (Phase 1.4). Every model is
`frozen=True` and `extra="forbid"` so unexpected fields surface as
construction-time errors rather than silent drift.

Money is represented in integer cents (`cost_cents: int`) to avoid the
floating-point precision drift that bites every cost dashboard sooner or
later. The frontend formats the integer back into a display string at the
last possible moment.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DateRange(BaseModel):
    """Inclusive-start, exclusive-end date window for a Cost Explorer query.

    AWS Cost Explorer treats `End` as exclusive, and we mirror that semantics
    here so the math stays consistent end-to-end. Construction validates that
    `from_date <= to_date`; an inverted range raises `ValueError` immediately.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_date: date = Field(description="Inclusive start of the window (UTC).")
    to_date: date = Field(description="Exclusive end of the window (UTC).")

    @model_validator(mode="after")
    def _validate_order(self) -> Self:
        if self.from_date > self.to_date:
            raise ValueError(
                f"DateRange: from_date ({self.from_date}) is after to_date ({self.to_date})"
            )
        return self


class CostByService(BaseModel):
    """One row of the by-service breakdown."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str = Field(description="AWS-canonical service name as returned by Cost Explorer.")
    cost_cents: int = Field(ge=0, description="Cost for the service in integer cents.")
    percent_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Cost as a percent of the total spend in the window (0-100).",
    )


class CostBreakdown(BaseModel):
    """Full response envelope for `GET /api/v1/cost/by-service`.

    `cache_hit` is the operational signal the dashboard surfaces so a human
    can tell at a glance whether the page was served from DynamoDB or paid a
    CE round trip. `queried_at` is the wall-clock instant the response was
    assembled, which lets the dashboard render staleness without trusting
    the client's clock.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_date: date
    to_date: date
    currency: str = Field(default="USD", description="ISO 4217 currency code.")
    services: tuple[CostByService, ...] = Field(
        description="Per-service rows, sorted descending by cost_cents."
    )
    total_cents: int = Field(ge=0, description="Sum of cost_cents across all rows.")
    cache_hit: bool = Field(description="True when the response was served from the cache.")
    queried_at: datetime = Field(description="UTC instant when the response was assembled.")


class TenantCostRow(BaseModel):
    """One row of the per-tenant cost rollup.

    Mirrors `TenantCostRow` in `services/admin/src/lib/types.ts`.
    `display_name` defaults to the tenant id so the table renders cleanly
    even before the user-directory join lands; future iterations will
    populate it from the auth-db.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(description="Stable tenant identifier (the rollup-table HK).")
    display_name: str = Field(description="Display-friendly tenant label.")
    cost_cents: int = Field(ge=0, description="Tenant cost across the window in integer cents.")
    percent_of_total: float = Field(
        ge=0.0,
        le=100.0,
        description="Cost as a percent of the total spend in the window (0-100).",
    )


class TenantCostBreakdown(BaseModel):
    """Full response envelope for `GET /api/v1/cost/by-tenant`.

    Same shape contract as `CostBreakdown` so the frontend table renderer
    can stay structurally identical (just a different row component).
    `cache_hit` and `queried_at` carry the same operational semantics as
    the by-service envelope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_date: date
    to_date: date
    currency: str = Field(default="USD", description="ISO 4217 currency code.")
    tenants: tuple[TenantCostRow, ...] = Field(
        description="Per-tenant rows, sorted descending by cost_cents."
    )
    total_cents: int = Field(ge=0, description="Sum of cost_cents across all rows.")
    cache_hit: bool = Field(description="True when the response was served from the cache.")
    queried_at: datetime = Field(description="UTC instant when the response was assembled.")


class CostAnomaly(BaseModel):
    """One anomaly entry surfaced on the dashboard.

    Mirrors `CostAnomaly` in `services/admin/src/lib/types.ts`. Money is
    integer cents to match the rest of the cost-api surface; the frontend
    formats it for display. `signature` is the dedup key the alert-state
    table uses (a stable hash of detector + tenant + dimension + window),
    which lets the route mark a freshly-detected anomaly as `suppressed`
    when the same signature is still inside its quiet-period window.

    `first_seen` and `last_seen` are ISO-8601 UTC instants (matching the
    `IsoTimestamp` type on the frontend). They differ when the same
    signature has been re-observed before its quiet period expired; the
    detector updates `last_seen` rather than emitting a fresh row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    signature: str = Field(description="Stable dedup hash of the anomaly's dimensions.")
    detector: str = Field(description="Detector name that emitted the anomaly.")
    tenant_id: str | None = Field(
        default=None,
        description="Tenant the anomaly is attributed to, if any.",
    )
    dimension_key: str = Field(
        description="Free-form dimension key the detector uses to label this anomaly.",
    )
    observed_cost_cents: int = Field(
        ge=0, description="Observed cost during the anomaly window in integer cents."
    )
    expected_cost_cents: int = Field(
        ge=0, description="Detector's expected cost for the same window in integer cents."
    )
    deviation_pct: float = Field(
        description=(
            "Signed percent deviation from expected: (observed - expected) / expected * 100."
        ),
    )
    first_seen: datetime = Field(description="UTC instant the detector first saw this anomaly.")
    last_seen: datetime = Field(description="UTC instant the detector most recently re-saw it.")
    suppressed: bool = Field(
        description="True when the anomaly's dedup signature is in its active quiet period.",
    )


class CostAnomalyList(BaseModel):
    """Full response envelope for `GET /api/v1/cost/anomalies`.

    `queried_at` carries the same operational semantics as the by-service
    and by-tenant envelopes. The list is unsorted on the wire because
    detector ordering and tenant ordering are both meaningful for
    different operator workflows; the frontend applies its own sort.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    anomalies: tuple[CostAnomaly, ...] = Field(
        description="Anomaly rows, dedup-filtered against the alert-state table.",
    )
    queried_at: datetime = Field(description="UTC instant when the response was assembled.")


class ForecastBucket(BaseModel):
    """One day's slot in the cost forecast surface.

    Mirrors `ForecastBucket` in `services/admin/src/lib/types.ts` so the
    SvelteKit dashboard can render the chart and per-day table without
    field-name translation. `predicted_cost_cents` is CE's `MeanValue`;
    `lower_bound_cents` and `upper_bound_cents` are the 95% prediction
    interval bounds. All money is integer cents to match the rest of
    the cost-api surface; the frontend formats it for display.
    """

    # `populate_by_name=True` lets construction by either the field name
    # (`bucket_date`, used in Python) or the alias (`date`, used on the
    # JSON wire). `serialize_by_alias=True` ensures the model serializes
    # back out under the alias so the JSON contract stays `date` -- the
    # frontend's `ForecastBucket` type expects exactly that key.
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    bucket_date: date = Field(
        alias="date",
        description="UTC day this bucket covers (inclusive).",
    )
    predicted_cost_cents: int = Field(
        ge=0,
        description="CE-predicted spend for the day in integer cents (mean of the interval).",
    )
    lower_bound_cents: int = Field(
        ge=0,
        description="Lower bound of the 95% prediction interval in integer cents.",
    )
    upper_bound_cents: int = Field(
        ge=0,
        description="Upper bound of the 95% prediction interval in integer cents.",
    )


class CostForecast(BaseModel):
    """Full response envelope for `GET /api/v1/cost/forecast`.

    Mirrors `CostForecast` in `services/admin/src/lib/types.ts`. The
    forecast covers `horizon_days` consecutive days starting from
    today (UTC). `model` identifies the forecasting backend so future
    iterations can swap CE for a custom model without breaking the
    contract; today the only value is `ce-builtin` (AWS Cost Explorer
    `GetCostForecast`). `queried_at` is the wall-clock instant the
    response was assembled, server-trusted so the dashboard can render
    staleness without trusting the client's clock.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: int = Field(
        gt=0,
        description="Number of consecutive days the forecast covers, starting today (UTC).",
    )
    buckets: tuple[ForecastBucket, ...] = Field(
        description="Per-day forecast buckets, ordered ascending by date.",
    )
    model: str = Field(
        description="Identifier for the forecasting backend (today: 'ce-builtin').",
    )
    cache_hit: bool = Field(
        default=False,
        description="True when the response was served from the DDB cache.",
    )
    queried_at: datetime = Field(description="UTC instant when the response was assembled.")


class CacheKey(BaseModel):
    """Structured cache key for cost-api results.

    The cache key string is a SHA-256 hash of a deterministic JSON
    serialization of the structured key fields. SHA-256 keeps the key
    fixed-width regardless of input cardinality, which keeps DynamoDB
    item-size predictable. The deterministic JSON serialization makes
    the same logical query map to the same key every time, regardless
    of dict-iteration ordering or formatting drift in the caller.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    query_kind: str = Field(
        description="Stable identifier for the query family, e.g. 'by-service'."
    )
    from_date: date
    to_date: date
    group_by: str | None = Field(default=None, description="Optional CE GroupBy dimension.")
    service_filter: str | None = Field(default=None, description="Optional CE service-name filter.")

    def to_string(self) -> str:
        """Render the deterministic cache_key string written to DynamoDB."""
        canonical = json.dumps(
            {
                "kind": self.query_kind,
                "from": self.from_date.isoformat(),
                "to": self.to_date.isoformat(),
                "group_by": self.group_by,
                "service_filter": self.service_filter,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{self.query_kind}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def utcnow() -> datetime:
    """UTC `datetime.now()` helper. Centralized so tests can monkeypatch one symbol."""
    return datetime.now(UTC)
