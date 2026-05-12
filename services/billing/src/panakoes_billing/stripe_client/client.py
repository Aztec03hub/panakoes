"""Stripe SDK wrapper used by Billing route handlers.

The `StripeAdapter` Protocol describes the small surface route handlers
need: create a Checkout Session, create a Customer Portal session,
verify a webhook signature, and parse a webhook payload into a typed
event dict. The default `StripeSDKAdapter` calls the real `stripe`
SDK; tests inject a fake adapter so unit tests never call the real
Stripe API.

Stripe is in TEST mode for this entire slice. The `Settings` validator
rejects any non-`sk_test_` API key at startup, and there is no live-key
code path anywhere in this package.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

import stripe

from panakoes_billing.config import Settings


class StripeSignatureError(Exception):
    """Raised when a webhook signature fails verification.

    Wrapping the SDK's `SignatureVerificationError` here lets the route
    handler catch a project-owned exception type, which keeps the
    public/private boundary clean and the test surface narrow.
    """


class StripeAdapter(Protocol):
    """Protocol describing the Stripe surface the routes need."""

    def create_checkout_session(
        self,
        *,
        customer_email: str,
        client_reference_id: str,
        price_id: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        """Create a Stripe Checkout Session and return the response dict."""
        ...

    def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> dict[str, Any]:
        """Create a Stripe Customer Portal session and return the response dict."""
        ...

    def create_customer(
        self,
        *,
        email: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        """Create a Stripe Customer and return the response dict."""
        ...

    def verify_webhook_signature(
        self,
        *,
        payload: bytes,
        signature_header: str,
        secret: str,
    ) -> dict[str, Any]:
        """Verify the Stripe-Signature header and return the parsed event.

        Raises `StripeSignatureError` if the signature does not match.
        """
        ...


class StripeSDKAdapter:
    """Default `StripeAdapter` backed by the `stripe` Python SDK.

    The constructor pins the SDK to the configured TEST-mode API key
    and a sensible default API version so subtle Stripe API changes
    don't surprise us between deploys. Route handlers depend on the
    Protocol, not this class, so tests substitute a fake.
    """

    def __init__(self, settings: Settings) -> None:
        """Configure the SDK with the TEST-mode API key from settings."""
        self._settings = settings
        # The SDK reads `stripe.api_key` as a module-level global. Setting
        # it on construction means every subsequent SDK call this adapter
        # makes uses the configured TEST key.
        stripe.api_key = settings.stripe_api_key

    def create_checkout_session(
        self,
        *,
        customer_email: str,
        client_reference_id: str,
        price_id: str,
        quantity: int,
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        """Create a Checkout Session in TEST mode and return it as a dict."""
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=customer_email,
            client_reference_id=client_reference_id,
            line_items=[{"price": price_id, "quantity": quantity}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        # The SDK returns a `Session` object that behaves like a dict
        # (it implements `__iter__` over its keys). We cast through
        # `Any` because the `stripe` package ships without inline types.
        return cast("dict[str, Any]", dict(cast("Any", session)))

    def create_portal_session(
        self,
        *,
        customer_id: str,
        return_url: str,
    ) -> dict[str, Any]:
        """Create a Customer Portal session in TEST mode and return it as a dict."""
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return cast("dict[str, Any]", dict(cast("Any", session)))

    def create_customer(
        self,
        *,
        email: str,
        metadata: dict[str, str],
    ) -> dict[str, Any]:
        """Create a Stripe Customer in TEST mode and return it as a dict."""
        customer = stripe.Customer.create(email=email, metadata=metadata)
        return cast("dict[str, Any]", dict(cast("Any", customer)))

    def verify_webhook_signature(
        self,
        *,
        payload: bytes,
        signature_header: str,
        secret: str,
    ) -> dict[str, Any]:
        """Verify the signature header and return the parsed event dict."""
        try:
            # The `stripe` package ships untyped, so the SDK call is
            # opaque to mypy. We `cast` the result so the rest of this
            # function operates on a typed value.
            event = cast(
                "Any",
                stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
                    payload=payload,
                    sig_header=signature_header,
                    secret=secret,
                ),
            )
        except stripe.SignatureVerificationError as exc:
            raise StripeSignatureError(str(exc)) from exc
        except ValueError as exc:
            # Malformed JSON ends up here; treat it as a signature failure
            # for the purposes of the public boundary so callers handle
            # both cases identically.
            raise StripeSignatureError(str(exc)) from exc
        return cast("dict[str, Any]", dict(event))
