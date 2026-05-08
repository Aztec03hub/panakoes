"""Tests for `panakoes_models.billing`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from panakoes_models.billing import BillingTier, Subscription

VALID_SUBSCRIPTION: dict[str, object] = {
    "id": "sub_internal_001",
    "user_id": "user_abcdefghijkl",
    "tier": BillingTier.PRO,
    "stripe_customer_id": "cus_ABCDEFG",
    "stripe_subscription_id": "sub_HIJKLMN",
    "status": "active",
    "started_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "current_period_end": datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
}


@pytest.mark.unit
def test_subscription_constructs() -> None:
    """A canonical valid Subscription constructs cleanly."""
    s = Subscription(**VALID_SUBSCRIPTION)  # type: ignore[arg-type]
    assert s.tier is BillingTier.PRO
    assert s.stripe_customer_id == "cus_ABCDEFG"


@pytest.mark.unit
@pytest.mark.parametrize("tier", list(BillingTier))
def test_subscription_accepts_every_tier(tier: BillingTier) -> None:
    """Every documented tier value is accepted."""
    s = Subscription(**{**VALID_SUBSCRIPTION, "tier": tier})  # type: ignore[arg-type]
    assert s.tier is tier


@pytest.mark.unit
def test_subscription_rejects_unknown_tier() -> None:
    """An undocumented tier is rejected."""
    with pytest.raises(ValidationError):
        Subscription(**{**VALID_SUBSCRIPTION, "tier": "enterprise"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_subscription_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        Subscription(**{**VALID_SUBSCRIPTION, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "field", ["id", "stripe_customer_id", "stripe_subscription_id", "status"]
)
def test_subscription_rejects_empty_required_strings(field: str) -> None:
    """Empty strings on required fields fail."""
    with pytest.raises(ValidationError):
        Subscription(**{**VALID_SUBSCRIPTION, field: ""})  # type: ignore[arg-type]


@pytest.mark.unit
def test_subscription_rejects_naive_started_at() -> None:
    """A naive `started_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        Subscription(**{**VALID_SUBSCRIPTION, "started_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_subscription_rejects_naive_current_period_end() -> None:
    """A naive `current_period_end` raises a validation error."""
    naive = datetime(2026, 6, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        Subscription(**{**VALID_SUBSCRIPTION, "current_period_end": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_subscription_round_trips_through_json() -> None:
    """JSON round-trip yields an equal subscription."""
    original = Subscription(**VALID_SUBSCRIPTION)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = Subscription.model_validate_json(raw)
    assert restored == original
