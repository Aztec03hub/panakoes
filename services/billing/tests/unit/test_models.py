"""Unit tests for `panakoes_billing.models`.

Models gate every wire payload, so each constraint here defends a
real client contract.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_billing.models import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    HealthResponse,
    PortalSessionRequest,
    PortalSessionResponse,
    SubscriptionView,
)


@pytest.mark.unit
def test_health_response_round_trip() -> None:
    """`HealthResponse` round-trips through a dict cleanly."""
    payload = HealthResponse(status="ok", service="billing")
    assert payload.model_dump() == {"status": "ok", "service": "billing"}


@pytest.mark.unit
def test_checkout_request_accepts_pro_without_seats() -> None:
    """`pro` tier is accepted without a seats value."""
    request = CheckoutSessionRequest(tier="pro")
    assert request.tier == "pro"
    assert request.seats is None


@pytest.mark.unit
def test_checkout_request_accepts_team_with_seats() -> None:
    """`team` tier accepts a seat count via the schema."""
    request = CheckoutSessionRequest(tier="team", seats=5)
    assert request.tier == "team"
    assert request.seats == 5


@pytest.mark.unit
def test_checkout_request_rejects_unknown_tier() -> None:
    """A tier outside the literal set is rejected."""
    with pytest.raises(ValidationError):
        CheckoutSessionRequest(tier="enterprise")  # type: ignore[arg-type]


@pytest.mark.unit
def test_checkout_request_rejects_extra_field() -> None:
    """Extra fields are rejected (no client-side spoofing of `user_id`, etc.)."""
    with pytest.raises(ValidationError):
        CheckoutSessionRequest.model_validate(
            {"tier": "pro", "user_id": "spoofed"}
        )


@pytest.mark.unit
def test_checkout_request_rejects_zero_seats() -> None:
    """`seats=0` violates the lower bound."""
    with pytest.raises(ValidationError):
        CheckoutSessionRequest(tier="team", seats=0)


@pytest.mark.unit
def test_checkout_response_serializes_url() -> None:
    """`CheckoutSessionResponse` carries the URL through serialization."""
    body = CheckoutSessionResponse(checkout_url="https://example.test/url")
    assert body.model_dump()["checkout_url"] == "https://example.test/url"


@pytest.mark.unit
def test_portal_response_serializes_url() -> None:
    """`PortalSessionResponse` carries the URL through serialization."""
    body = PortalSessionResponse(url="https://example.test/portal")
    assert body.model_dump()["url"] == "https://example.test/portal"


@pytest.mark.unit
def test_portal_request_round_trips() -> None:
    """`PortalSessionRequest` carries the return URL through serialization."""
    req = PortalSessionRequest(return_url="https://panakoes.com/account")
    assert req.model_dump() == {"return_url": "https://panakoes.com/account"}


@pytest.mark.unit
def test_portal_request_rejects_empty_return_url() -> None:
    """Empty `return_url` violates the lower bound (defense in depth)."""
    with pytest.raises(ValidationError):
        PortalSessionRequest(return_url="")


@pytest.mark.unit
def test_portal_request_forbids_extra_fields() -> None:
    """Unknown fields are rejected so a typo in the SPA cannot silently no-op."""
    with pytest.raises(ValidationError):
        PortalSessionRequest.model_validate(
            {"return_url": "https://panakoes.com/account", "extra": "nope"}
        )


@pytest.mark.unit
def test_subscription_view_defaults_to_empty() -> None:
    """`SubscriptionView()` produces a fully-null view (free user shape)."""
    view = SubscriptionView()
    dump = view.model_dump()
    assert dump == {"tier": None, "status": None, "current_period_end": None}
