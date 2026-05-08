"""Unit tests for `panakoes_billing.config`.

The Settings class enforces TEST-mode by validating the Stripe API key
prefix at startup. That guard is security-critical and must hit 100%
coverage like the rest of the billing logic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_billing.config import Settings


@pytest.mark.unit
def test_settings_loads_test_mode_defaults() -> None:
    """`Settings()` exposes the documented test-pinned values when env vars are set."""
    settings = Settings()
    assert settings.service_name == "billing"
    assert settings.aws_region == "us-east-1"
    assert settings.stripe_api_key.startswith("sk_test_")
    assert settings.ddb_billing_table.endswith("-test")
    assert settings.jwt_audience == "panakoes-api-test"


@pytest.mark.unit
def test_settings_rejects_live_stripe_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `sk_live_...` key raises `ValidationError` at construction.

    This is the runtime invariant that makes "TEST mode only in this
    slice" an enforceable contract rather than a code-review hope.
    """
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_abc123")
    with pytest.raises(ValidationError) as exc:
        Settings()
    assert "sk_test_" in str(exc.value)


@pytest.mark.unit
def test_settings_rejects_arbitrary_non_test_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any non-test prefix is rejected (e.g. `rk_test_` restricted keys)."""
    monkeypatch.setenv("STRIPE_API_KEY", "rk_test_abc123")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.unit
def test_settings_accepts_other_test_prefixes_only_via_sk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty key is rejected; only `sk_test_*` is accepted."""
    monkeypatch.setenv("STRIPE_API_KEY", "")
    with pytest.raises(ValidationError):
        Settings()
