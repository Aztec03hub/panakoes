"""Pydantic request and response models for the Billing service."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The two paid tiers. `free` is implicit (no Stripe subscription).
Tier = Literal["pro", "team"]

# Subscription status mirrors the subset of Stripe statuses we care
# about. We collapse trialing/active to `active` and treat anything
# else as a non-paying state on read. The literal here is the wire
# value clients see.
SubscriptionStatus = Literal[
    "active",
    "past_due",
    "canceled",
    "incomplete",
    "incomplete_expired",
    "trialing",
    "unpaid",
    "paused",
]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class CheckoutSessionRequest(BaseModel):
    """Request body for `POST /billing/checkout-session`.

    `seats` only applies to the `team` tier. We accept it for both for
    a uniform shape; the route layer enforces the team-only constraint
    and a sane minimum (3 seats per the locked pricing decision).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    tier: Tier
    seats: int | None = Field(default=None, ge=1, le=10_000)


class CheckoutSessionResponse(BaseModel):
    """Response body for `POST /billing/checkout-session`."""

    checkout_url: str


class PortalSessionRequest(BaseModel):
    """Request body for `POST /billing/portal-session`.

    `return_url` is where Stripe sends the user when they close the
    Customer Portal. The route layer validates the URL against a
    Panakoes-owned allowlist so we never accept an open redirect.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    return_url: str = Field(min_length=1, max_length=2048)


class PortalSessionResponse(BaseModel):
    """Response body for `POST /billing/portal-session`."""

    url: str


class SubscriptionView(BaseModel):
    """Response body for `GET /billing/subscription`.

    `tier` is `null` for users who have never subscribed; `status` and
    `current_period_end` are populated only for users with a
    subscription record on file.
    """

    tier: Tier | None = None
    status: SubscriptionStatus | None = None
    current_period_end: datetime | None = None
