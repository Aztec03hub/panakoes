"""Billing models (skeletons for slice 1).

Slice-1 ships only the minimal `Subscription` shape needed for other
services to reference Stripe customer/subscription ids. Slice-2 will
add invoices, line items, and usage-meter records.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.users import USER_ID_PATTERN


class BillingTier(StrEnum):
    """Pricing tier mirroring the public Panakoes plans.

    Free / Pro ($12 mo) / Team ($30/seat, min 3 seats). The string
    values are the canonical identifiers used everywhere downstream.
    """

    FREE = "free"
    PRO = "pro"
    TEAM = "team"


_USER_ID_RE = re.compile(USER_ID_PATTERN)


class Subscription(BaseDomainModel):
    """A user's active Stripe subscription record (slice-1 skeleton).

    The Stripe ids are intentionally typed as plain strings so this lib
    has zero hard dependency on the Stripe SDK shape; consumers that
    need richer Stripe objects should fetch them via the Stripe API
    using these ids as the lookup key.
    """

    id: str = Field(..., min_length=1, description="Internal subscription identifier")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    tier: BillingTier
    stripe_customer_id: str = Field(
        ..., min_length=1, description="Stripe customer id (`cus_...`)"
    )
    stripe_subscription_id: str = Field(
        ..., min_length=1, description="Stripe subscription id (`sub_...`)"
    )
    status: str = Field(
        ...,
        min_length=1,
        description="Stripe subscription.status string (e.g. `active`, `past_due`)",
    )
    started_at: datetime
    current_period_end: datetime

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        """Reject user ids that do not match the documented pattern."""
        if not _USER_ID_RE.match(value):
            raise ValueError(
                f"user_id must match pattern {USER_ID_PATTERN!r} (got: {value!r})"
            )
        return value

    @field_validator("started_at", "current_period_end")
    @classmethod
    def _validate_timestamps_utc(cls, value: datetime) -> datetime:
        """Require timezone-aware UTC datetimes on both timestamps."""
        return validate_utc_datetime(value)
