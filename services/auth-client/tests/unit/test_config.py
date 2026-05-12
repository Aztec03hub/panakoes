"""Tests for `from_env`."""

from __future__ import annotations

import pytest

from panakoes_auth_client import JwtConfigError, JwtValidator, from_env


@pytest.mark.unit
def test_from_env_constructs_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three vars set produces a configured validator."""
    monkeypatch.setenv("JWT_SECRET", "s")
    monkeypatch.setenv("JWT_ISSUER", "https://auth.example")
    monkeypatch.setenv("JWT_AUDIENCE", "api")

    validator = from_env()

    assert isinstance(validator, JwtValidator)
    assert validator._secret == "s"
    assert validator._issuer == "https://auth.example"
    assert validator._audience == "api"


@pytest.mark.unit
@pytest.mark.parametrize(
    "missing_var",
    ["JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE"],
)
def test_from_env_raises_when_a_single_var_is_missing(
    monkeypatch: pytest.MonkeyPatch, missing_var: str
) -> None:
    """Each of the three env vars is independently required."""
    for name in ("JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE"):
        if name != missing_var:
            monkeypatch.setenv(name, "value")

    with pytest.raises(JwtConfigError, match=missing_var):
        from_env()


@pytest.mark.unit
def test_from_env_raises_when_all_vars_missing() -> None:
    """All three names appear in the error so deployment can fix at once."""
    with pytest.raises(JwtConfigError) as excinfo:
        from_env()

    msg = str(excinfo.value)
    assert "JWT_SECRET" in msg
    assert "JWT_ISSUER" in msg
    assert "JWT_AUDIENCE" in msg


@pytest.mark.unit
def test_from_env_treats_empty_string_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty values are usually unresolved Secrets Manager refs; reject them."""
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("JWT_ISSUER", "https://auth.example")
    monkeypatch.setenv("JWT_AUDIENCE", "api")

    with pytest.raises(JwtConfigError, match="JWT_SECRET"):
        from_env()


@pytest.mark.unit
def test_from_env_uses_jwks_when_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """`JWT_PUBLIC_JWKS_URL` activates the JWKS-backed RS256 path."""
    from panakoes_auth_client import JwksJwtValidator

    monkeypatch.setenv("JWT_PUBLIC_JWKS_URL", "https://auth.example/.well-known/jwks.json")
    monkeypatch.setenv("JWT_ISSUER", "https://auth.example")
    monkeypatch.setenv("JWT_AUDIENCE", "api")

    validator = from_env()
    assert isinstance(validator, JwksJwtValidator)


@pytest.mark.unit
def test_from_env_jwks_path_requires_issuer_and_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When JWKS mode is selected, ISSUER + AUDIENCE remain required."""
    monkeypatch.setenv("JWT_PUBLIC_JWKS_URL", "https://auth.example/.well-known/jwks.json")
    # JWT_ISSUER and JWT_AUDIENCE intentionally unset.

    with pytest.raises(JwtConfigError, match="JWT_ISSUER"):
        from_env()


@pytest.mark.unit
def test_from_env_jwks_does_not_require_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`JWT_SECRET` is not required when JWKS mode is selected."""
    from panakoes_auth_client import JwksJwtValidator

    monkeypatch.setenv("JWT_PUBLIC_JWKS_URL", "https://auth.example/.well-known/jwks.json")
    monkeypatch.setenv("JWT_ISSUER", "https://auth.example")
    monkeypatch.setenv("JWT_AUDIENCE", "api")
    # JWT_SECRET intentionally not set.

    validator = from_env()
    assert isinstance(validator, JwksJwtValidator)
