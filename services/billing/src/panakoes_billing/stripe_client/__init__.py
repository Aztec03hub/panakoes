"""Thin Stripe SDK wrapper.

The wrapper exists so route handlers depend on a small, intention-
revealing surface (`create_checkout_session`, `create_portal_session`,
`verify_webhook_signature`, `parse_event`) instead of touching the
`stripe` module directly. Tests inject a fake implementation; the real
implementation calls `stripe` in TEST mode.
"""

from panakoes_billing.stripe_client.client import (
    StripeAdapter,
    StripeSDKAdapter,
    StripeSignatureError,
)

__all__ = [
    "StripeAdapter",
    "StripeSDKAdapter",
    "StripeSignatureError",
]
