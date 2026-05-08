"""Settings module for the Billing service.

Environment-driven configuration via `pydantic-settings`. The service
reads the same JWT settings the Auth service uses (HS256), the Stripe
TEST-mode API and webhook secrets, plus the DynamoDB table that records
billing events.

This slice is TEST-mode only: `stripe_api_key` is required to start
with `sk_test_` and the service refuses to boot otherwise. That guard
is the simplest way to make "no live keys ever" a runtime invariant
rather than a code-review aspiration.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for the Billing service.

    Every field maps to a single environment variable (case-insensitive).
    Defaults are dev-friendly placeholders so the service can boot in a
    test runner without external infra; production env vars override
    them. The Stripe API key is the one exception: there is no safe
    default, so we ship a placeholder TEST key and reject any value
    that does not start with `sk_test_`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "billing"
    log_level: str = "INFO"
    aws_region: str = "us-east-1"

    # JWT contract: HS256 shared secret with the Auth service. Names
    # mirror ADR-022 wording so a future RS256 swap is mechanical.
    jwt_secret: str = "dev-only-secret-replace-in-production"  # noqa: S105
    jwt_issuer: str = "https://auth.panakoes.com"
    jwt_audience: str = "panakoes-api"

    # Stripe TEST-mode credentials. Live keys are explicitly forbidden
    # by the validator below: any `sk_live_...` value raises at startup.
    stripe_api_key: str = "sk_test_placeholder_replace_in_env"
    stripe_webhook_secret: str = "whsec_placeholder_replace_in_env"  # noqa: S105

    # Stripe Price IDs for the two paid tiers. Real values are populated
    # in env vars per environment; defaults are TEST-mode placeholders
    # the integration tests never call.
    stripe_price_pro: str = "price_test_pro_placeholder"
    stripe_price_team: str = "price_test_team_placeholder"

    # Stripe Customer Portal return URL and Checkout success/cancel URLs.
    # Real values come from env; defaults route to a localhost dev page.
    stripe_success_url: str = "http://localhost:3000/billing/success"
    stripe_cancel_url: str = "http://localhost:3000/billing/cancel"
    stripe_portal_return_url: str = "http://localhost:3000/account"

    # DynamoDB billing events table (Terraform-managed).
    ddb_billing_table: str = "panakoes-dev-billing-events"

    @field_validator("stripe_api_key")
    @classmethod
    def _reject_live_key(cls, value: str) -> str:
        """Reject any non-test Stripe API key at startup.

        The contract for this slice is "TEST mode only." Encoding it as
        a config validator means the service literally cannot boot with
        a live key, even if the rest of the codebase grew a path that
        attempted to use one. Defense in depth.
        """
        if not value.startswith("sk_test_"):
            raise ValueError(
                "stripe_api_key must start with 'sk_test_' (TEST mode only in this slice)"
            )
        return value
