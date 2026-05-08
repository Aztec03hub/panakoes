"""Unit tests for `panakoes_notification.config.Settings`."""

from __future__ import annotations

import pytest

from panakoes_notification.config import Settings


@pytest.mark.unit
def test_settings_loads_test_env() -> None:
    """`Settings()` reflects the env vars pinned by the autouse fixture."""
    settings = Settings()
    assert settings.service_name == "notification"
    assert settings.aws_region == "us-east-1"
    assert settings.jwt_issuer == "https://auth.panakoes.test"
    assert settings.jwt_audience == "panakoes-api-test"
    assert settings.ses_from_address == "no-reply@panakoes.test"
    assert settings.ddb_notification_table == "panakoes-notification-test"


@pytest.mark.unit
def test_settings_default_pagination_values() -> None:
    """Pagination defaults match the documented contract."""
    settings = Settings()
    assert settings.list_default_limit == 25
    assert settings.list_max_limit == 100


@pytest.mark.unit
def test_settings_default_webhook_policy() -> None:
    """Webhook retry defaults match the brief: 3 attempts, base 1s."""
    settings = Settings()
    assert settings.webhook_max_attempts == 3
    assert settings.webhook_base_backoff_seconds == 1.0
