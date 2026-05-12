"""Pins the JWT validator env-var contract for the Ingestion API.

The project-wide rule (see `CONTRIBUTING.md`) is that JWT validators
read `JWT_SECRET` / `JWT_ISSUER` / `JWT_AUDIENCE` (no `AUTH_` prefix).
The signer side in `services/auth` keeps `AUTH_JWT_*`. A silent
regression to the old `AUTH_JWT_*` prefix on this service caused a
production-shaped bug class once already; this test exists to fail
loudly if the contract drifts.
"""

from __future__ import annotations

import pytest

from panakoes_ingestion_api.config import Settings


def test_settings_resolves_jwt_secret_from_jwt_secret_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`JWT_SECRET` (not `AUTH_JWT_SECRET`) populates `settings.jwt_secret`."""
    monkeypatch.setenv("JWT_SECRET", "from-jwt-secret-env-var-32bytes-min")
    monkeypatch.setenv("JWT_ISSUER", "https://issuer.test")
    monkeypatch.setenv("JWT_AUDIENCE", "audience.test")
    settings = Settings()
    assert settings.jwt_secret == "from-jwt-secret-env-var-32bytes-min"
    assert settings.jwt_issuer == "https://issuer.test"
    assert settings.jwt_audience == "audience.test"


def test_settings_ignores_legacy_auth_jwt_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`AUTH_JWT_SECRET` must NOT populate `settings.jwt_secret`.

    pydantic-settings uses field name `jwt_secret`, which maps to env
    var `JWT_SECRET`. The legacy `AUTH_JWT_SECRET` env var is unrelated
    and must be ignored. If this test fails, the rename regressed.
    """
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("AUTH_JWT_SECRET", "legacy-prefix-should-be-ignored")
    settings = Settings()
    assert settings.jwt_secret != "legacy-prefix-should-be-ignored"
