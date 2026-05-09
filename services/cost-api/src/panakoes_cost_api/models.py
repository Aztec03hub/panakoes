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
    service_filter: str | None = Field(
        default=None, description="Optional CE service-name filter."
    )

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
