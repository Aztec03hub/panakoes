"""Unit tests for the small pure helpers in `routes/billing.py`.

The integration tests already exercise these through the HTTP surface,
but a few branches (non-dict `data.object`, non-string `metadata.user_id`,
non-list `items.data`) are easier to drive directly. We also call the
dependency factories directly so the production-path code (no
`dependency_overrides`) is covered too.
"""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_billing.config import Settings
from panakoes_billing.routes.billing import (
    _apply_subscription_state,
    _extract_subscription_attributes,
    _is_allowed_return_url,
    _plan_from_attributes,
    _resolve_price_for_tier,
    _resolve_quantity,
    _user_id_from_event,
    get_event_store,
    get_settings,
    get_stripe_adapter,
    get_subscription_store,
)
from panakoes_billing.storage.dynamodb import BillingEventStore
from panakoes_billing.storage.subscriptions import SubscriptionStore
from panakoes_billing.stripe_client.client import StripeSDKAdapter


@pytest.mark.unit
def test_get_settings_returns_fresh_settings_instance() -> None:
    """The dependency factory builds a `Settings` per call."""
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.service_name == "billing"


@pytest.mark.unit
def test_get_event_store_uses_settings(test_settings: Settings) -> None:
    """`get_event_store` builds a store bound to the configured table name."""
    store = get_event_store(test_settings)
    assert isinstance(store, BillingEventStore)
    assert store.table_name == test_settings.ddb_billing_table


@pytest.mark.unit
def test_get_stripe_adapter_uses_settings(test_settings: Settings) -> None:
    """`get_stripe_adapter` builds the SDK adapter using configured settings."""
    adapter = get_stripe_adapter(test_settings)
    assert isinstance(adapter, StripeSDKAdapter)


@pytest.mark.unit
def test_get_subscription_store_uses_settings(test_settings: Settings) -> None:
    """`get_subscription_store` builds a store bound to the configured table."""
    store = get_subscription_store(test_settings)
    assert isinstance(store, SubscriptionStore)
    assert store.table_name == test_settings.ddb_subscriptions_table


@pytest.mark.unit
def test_plan_from_attributes_resolves_each_tier() -> None:
    """`_plan_from_attributes` returns the documented plan for each tier."""
    assert _plan_from_attributes({"tier": "team"}) == "team"
    assert _plan_from_attributes({"tier": "pro"}) == "pro"
    assert _plan_from_attributes({}) == "free"
    assert _plan_from_attributes({"tier": "platinum"}) == "free"


@pytest.mark.unit
def test_apply_subscription_state_returns_true_when_no_subscription_id() -> None:
    """A subscription event with no subscription id is acknowledged with no write."""
    from unittest.mock import MagicMock

    store = MagicMock(spec=SubscriptionStore)
    result = _apply_subscription_state(
        event_type="customer.subscription.created",
        event_id="evt_1",
        tenant_id="u",
        event_object={"customer": "cus"},
        attributes={},
        subscription_store=store,
    )
    assert result is True
    store.upsert.assert_not_called()


@pytest.mark.unit
def test_apply_subscription_state_handles_malformed_period_end() -> None:
    """A malformed `current_period_end` string falls back to None."""
    from unittest.mock import MagicMock

    store = MagicMock(spec=SubscriptionStore)
    store.upsert.return_value = True
    _apply_subscription_state(
        event_type="customer.subscription.updated",
        event_id="evt_1",
        tenant_id="u",
        event_object={"cancel_at": 1_900_000_000, "quantity": 7},
        attributes={
            "stripe_subscription_id": "sub_1",
            "tier": "team",
            "current_period_end": "not-a-date",
            "stripe_customer_id": "cus_1",
        },
        subscription_store=store,
    )
    store.upsert.assert_called_once()
    kwargs = store.upsert.call_args.kwargs
    assert kwargs["current_period_end"] is None
    assert kwargs["cancel_at"] is not None
    assert kwargs["quantity"] == 7


@pytest.mark.unit
@pytest.mark.parametrize(
    "event_object",
    [
        {"items": {"data": []}},  # empty list
        {"items": {"data": "not-a-list"}},  # not a list
        {"items": {"data": ["not-a-dict"]}},  # first element not dict
        {"items": {"data": [{}]}},  # dict but no quantity
        {"items": "not-a-dict"},  # items not a dict
    ],
)
def test_apply_subscription_state_quantity_fallbacks_to_one(event_object: dict[str, Any]) -> None:
    """Every malformed items shape falls back to quantity=1 without raising."""
    from unittest.mock import MagicMock

    store = MagicMock(spec=SubscriptionStore)
    store.upsert.return_value = True
    _apply_subscription_state(
        event_type="customer.subscription.created",
        event_id="evt_1",
        tenant_id="u",
        event_object=event_object,
        attributes={"stripe_subscription_id": "sub_1", "tier": "pro"},
        subscription_store=store,
    )
    assert store.upsert.call_args.kwargs["quantity"] == 1


@pytest.mark.unit
def test_apply_subscription_state_reads_quantity_from_items_when_root_missing() -> None:
    """If `quantity` is not at the root, fall back to items[0].quantity."""
    from unittest.mock import MagicMock

    store = MagicMock(spec=SubscriptionStore)
    store.upsert.return_value = True
    _apply_subscription_state(
        event_type="customer.subscription.created",
        event_id="evt_1",
        tenant_id="u",
        event_object={
            "items": {"data": [{"quantity": 4}]},
        },
        attributes={
            "stripe_subscription_id": "sub_1",
            "tier": "team",
        },
        subscription_store=store,
    )
    assert store.upsert.call_args.kwargs["quantity"] == 4


@pytest.mark.unit
def test_resolve_price_for_pro(test_settings: Settings) -> None:
    """`_resolve_price_for_tier('pro')` returns the configured pro price."""
    assert _resolve_price_for_tier("pro", test_settings) == test_settings.stripe_price_pro


@pytest.mark.unit
def test_resolve_price_for_team(test_settings: Settings) -> None:
    """`_resolve_price_for_tier('team')` returns the configured team price."""
    assert _resolve_price_for_tier("team", test_settings) == test_settings.stripe_price_team


@pytest.mark.unit
def test_resolve_quantity_pro_ignores_seats() -> None:
    """`_resolve_quantity('pro', N)` is always 1 regardless of `N`."""
    assert _resolve_quantity("pro", None) == 1
    assert _resolve_quantity("pro", 5) == 1


@pytest.mark.unit
def test_resolve_quantity_team_returns_seats() -> None:
    """`_resolve_quantity('team', N)` returns `N` when `N >= 3`."""
    assert _resolve_quantity("team", 3) == 3
    assert _resolve_quantity("team", 50) == 50


@pytest.mark.unit
def test_user_id_from_event_returns_client_reference_id() -> None:
    """`client_reference_id` wins when present."""
    assert _user_id_from_event({"client_reference_id": "u_42"}) == "u_42"


@pytest.mark.unit
def test_user_id_from_event_falls_back_to_metadata() -> None:
    """`metadata.user_id` is used when `client_reference_id` is missing."""
    obj: dict[str, Any] = {"metadata": {"user_id": "u_meta"}}
    assert _user_id_from_event(obj) == "u_meta"


@pytest.mark.unit
def test_user_id_from_event_returns_none_when_metadata_user_id_non_string() -> None:
    """A non-string `metadata.user_id` is treated as missing."""
    obj: dict[str, Any] = {"metadata": {"user_id": 42}}
    assert _user_id_from_event(obj) is None


@pytest.mark.unit
def test_user_id_from_event_returns_none_when_metadata_not_dict() -> None:
    """A non-dict `metadata` is ignored."""
    obj: dict[str, Any] = {"metadata": "not-a-dict"}
    assert _user_id_from_event(obj) is None


@pytest.mark.unit
def test_user_id_from_event_returns_none_when_empty() -> None:
    """An empty event object returns `None`."""
    assert _user_id_from_event({}) is None


@pytest.mark.unit
def test_user_id_from_event_handles_empty_string_client_ref() -> None:
    """Empty-string `client_reference_id` falls through to metadata lookup."""
    obj: dict[str, Any] = {
        "client_reference_id": "",
        "metadata": {"user_id": "u_fallback"},
    }
    assert _user_id_from_event(obj) == "u_fallback"


@pytest.mark.unit
def test_extract_attributes_with_no_customer_or_subscription() -> None:
    """A bare event object yields only the event type."""
    attrs = _extract_subscription_attributes("invoice.paid", {})
    assert attrs == {"stripe_event_type": "invoice.paid"}


@pytest.mark.unit
def test_extract_attributes_uses_object_id_when_subscription_missing() -> None:
    """When `subscription` is absent, the top-level `id` is used as the sub id."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"id": "sub_xyz"},
    )
    assert attrs["stripe_subscription_id"] == "sub_xyz"


@pytest.mark.unit
def test_extract_attributes_skips_non_int_period_end() -> None:
    """A non-int `current_period_end` does not appear in the attributes."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"current_period_end": "2026-01-01"},
    )
    assert "current_period_end" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_when_items_data_not_list() -> None:
    """A malformed `items.data` does not crash the helper."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"items": {"data": "not-a-list"}},
    )
    assert "stripe_price_id" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_when_items_data_first_not_dict() -> None:
    """A non-dict first element in `items.data` does not crash the helper."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"items": {"data": ["not-a-dict"]}},
    )
    assert "stripe_price_id" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_when_price_not_dict() -> None:
    """A non-dict `price` field is ignored."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"items": {"data": [{"price": "not-a-dict"}]}},
    )
    assert "stripe_price_id" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_when_price_id_non_string() -> None:
    """A non-string `price.id` is ignored."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"items": {"data": [{"price": {"id": 42}}]}},
    )
    assert "stripe_price_id" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_empty_string_status() -> None:
    """An empty-string status is treated as missing."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"status": ""},
    )
    assert "status" not in attrs


@pytest.mark.unit
def test_extract_attributes_skips_non_string_customer() -> None:
    """A non-string `customer` is ignored."""
    attrs = _extract_subscription_attributes(
        "customer.subscription.updated",
        {"customer": 42},
    )
    assert "stripe_customer_id" not in attrs


# ---------------------------------------------------------------------------
# _is_allowed_return_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://dmaopcm3hnxog.cloudfront.net/account",
        "https://dmaopcm3hnxog.cloudfront.net/account?foo=bar",
        "https://panakoes.com/account",
        "https://panakoes.com/billing/return",
        # Case-insensitive host: Stripe + browsers normalize but be defensive.
        "https://PANAKOES.com/account",
    ],
)
def test_is_allowed_return_url_accepts_panakoes_origins(url: str) -> None:
    """Allowlisted origins return True regardless of path / query."""
    assert _is_allowed_return_url(url) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",
        "http://panakoes.com/account",  # http downgrade
        "https://panakoes.com.evil.com/account",  # subdomain confusion
        "https://evilpanakoes.com/account",
        "ftp://panakoes.com/account",
        "https:///account",  # missing host
        "",  # empty string
        "not-a-url",
    ],
)
def test_is_allowed_return_url_rejects_other_origins(url: str) -> None:
    """Anything not on the allowlist returns False."""
    assert _is_allowed_return_url(url) is False
