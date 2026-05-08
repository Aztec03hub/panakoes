"""Unit test for the template service `Settings` defaults."""

from __future__ import annotations

import pytest

from template_service.config import Settings


@pytest.mark.unit
def test_settings_loads_defaults() -> None:
    """`Settings()` exposes the documented defaults when no env vars override them."""
    settings = Settings()
    assert settings.service_name == "template"
    assert settings.log_level == "INFO"
    assert settings.aws_region == "us-east-1"
