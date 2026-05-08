"""Unit tests for `panakoes_billing.stripe_client.client`.

We never call the real Stripe API. The SDK adapter routes every call
through module-level `stripe.*` namespaces; we monkey-patch those
namespaces to capture the request and return canned responses (or
raise the SDK exceptions we care about).
"""

from __future__ import annotations

from typing import Any

import pytest
import stripe

from panakoes_billing.config import Settings
from panakoes_billing.stripe_client import (
    StripeAdapter,
    StripeSDKAdapter,
    StripeSignatureError,
)


def _settings() -> Settings:
    """Build a fresh test-pinned Settings."""
    return Settings()


@pytest.mark.unit
def test_adapter_protocol_is_runtime_satisfied() -> None:
    """`StripeSDKAdapter` satisfies the documented `StripeAdapter` Protocol."""
    settings = _settings()
    adapter: StripeAdapter = StripeSDKAdapter(settings)
    assert hasattr(adapter, "create_checkout_session")
    assert hasattr(adapter, "create_portal_session")
    assert hasattr(adapter, "verify_webhook_signature")


@pytest.mark.unit
def test_constructor_pins_stripe_api_key_to_settings() -> None:
    """Constructing the adapter sets `stripe.api_key` to the configured key."""
    settings = _settings()
    StripeSDKAdapter(settings)
    assert stripe.api_key == settings.stripe_api_key
    assert stripe.api_key.startswith("sk_test_")


@pytest.mark.unit
def test_create_checkout_session_calls_sdk_with_documented_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter forwards args to `stripe.checkout.Session.create` as documented."""
    captured: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"id": "cs_test", "url": "https://stripe.test/checkout"}

    monkeypatch.setattr("stripe.checkout.Session.create", _fake_create)

    adapter = StripeSDKAdapter(_settings())
    response = adapter.create_checkout_session(
        customer_email="alice@example.com",
        client_reference_id="user_42",
        price_id="price_test_pro",
        quantity=1,
        success_url="https://app.test/success",
        cancel_url="https://app.test/cancel",
    )

    assert response["url"] == "https://stripe.test/checkout"
    assert captured["mode"] == "subscription"
    assert captured["customer_email"] == "alice@example.com"
    assert captured["client_reference_id"] == "user_42"
    assert captured["line_items"] == [{"price": "price_test_pro", "quantity": 1}]
    assert captured["success_url"] == "https://app.test/success"
    assert captured["cancel_url"] == "https://app.test/cancel"


@pytest.mark.unit
def test_create_portal_session_calls_sdk_with_documented_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter forwards args to `stripe.billing_portal.Session.create`."""
    captured: dict[str, Any] = {}

    def _fake_create(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"id": "bps_test", "url": "https://stripe.test/portal"}

    monkeypatch.setattr("stripe.billing_portal.Session.create", _fake_create)

    adapter = StripeSDKAdapter(_settings())
    response = adapter.create_portal_session(
        customer_id="cus_42",
        return_url="https://app.test/account",
    )
    assert response["url"] == "https://stripe.test/portal"
    assert captured == {"customer": "cus_42", "return_url": "https://app.test/account"}


@pytest.mark.unit
def test_verify_webhook_signature_returns_event_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid signature returns the parsed event as a dict."""
    captured: dict[str, Any] = {}

    def _fake_construct(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"id": "evt_test", "type": "invoice.paid"}

    monkeypatch.setattr("stripe.Webhook.construct_event", _fake_construct)

    adapter = StripeSDKAdapter(_settings())
    event = adapter.verify_webhook_signature(
        payload=b"{}",
        signature_header="t=1,v1=abc",
        secret="whsec_test",
    )
    assert event == {"id": "evt_test", "type": "invoice.paid"}
    assert captured["payload"] == b"{}"
    assert captured["sig_header"] == "t=1,v1=abc"
    assert captured["secret"] == "whsec_test"


@pytest.mark.unit
def test_verify_webhook_signature_raises_on_signature_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `SignatureVerificationError` from the SDK becomes `StripeSignatureError`."""

    def _fake_construct(**_: Any) -> dict[str, Any]:
        raise stripe.SignatureVerificationError("bad signature", "sig_header")

    monkeypatch.setattr("stripe.Webhook.construct_event", _fake_construct)

    adapter = StripeSDKAdapter(_settings())
    with pytest.raises(StripeSignatureError) as exc:
        adapter.verify_webhook_signature(
            payload=b"{}",
            signature_header="garbage",
            secret="whsec_test",
        )
    assert "bad signature" in str(exc.value)


@pytest.mark.unit
def test_verify_webhook_signature_raises_on_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ValueError` (malformed JSON) also becomes `StripeSignatureError`."""

    def _fake_construct(**_: Any) -> dict[str, Any]:
        raise ValueError("malformed json body")

    monkeypatch.setattr("stripe.Webhook.construct_event", _fake_construct)

    adapter = StripeSDKAdapter(_settings())
    with pytest.raises(StripeSignatureError) as exc:
        adapter.verify_webhook_signature(
            payload=b"not-json",
            signature_header="t=1,v1=abc",
            secret="whsec_test",
        )
    assert "malformed" in str(exc.value)
