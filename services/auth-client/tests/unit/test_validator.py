"""Tests for the `JwtValidator` class.

Cover the happy path plus every failure mode listed in the public
API: expired, bad signature, wrong issuer, wrong audience, malformed
token, missing required claim, wrong-typed claim.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from freezegun import freeze_time

from panakoes_auth_client import JwtClaims, JwtInvalidError, JwtValidator


@pytest.mark.unit
def test_validate_returns_jwt_claims_for_valid_token(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A correctly-signed token round-trips into a `JwtClaims` instance."""
    token = make_token(overrides={"sub": "user_xyz", "scopes": ["read", "write"]})

    claims = validator.validate(token)

    assert isinstance(claims, JwtClaims)
    assert claims.sub == "user_xyz"
    assert claims.scopes == ["read", "write"]


@pytest.mark.unit
def test_default_algorithms_is_hs256() -> None:
    """The default algorithm list is `['HS256']` so callers can omit it."""
    validator = JwtValidator(secret="x", issuer="i", audience="a")

    assert validator._algorithms == ["HS256"]


@pytest.mark.unit
def test_explicit_algorithms_override_default() -> None:
    """An explicit `algorithms` argument is honored."""
    validator = JwtValidator(secret="x", issuer="i", audience="a", algorithms=["HS512"])

    assert validator._algorithms == ["HS512"]


@pytest.mark.unit
def test_jti_is_optional(validator: JwtValidator, make_token: Callable[..., str]) -> None:
    """A token without `jti` validates and returns `jti = None`."""
    token = make_token()

    claims = validator.validate(token)

    assert claims.jti is None


@pytest.mark.unit
def test_jti_is_preserved_when_present(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A `jti` claim in the payload is copied through to `JwtClaims`."""
    token = make_token(overrides={"jti": "session_123"})

    claims = validator.validate(token)

    assert claims.jti == "session_123"


@pytest.mark.unit
def test_scopes_default_to_empty_list(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token without `scopes` validates and returns an empty list."""
    token = make_token()

    claims = validator.validate(token)

    assert claims.scopes == []


@pytest.mark.unit
def test_expired_token_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token whose `exp` is in the past raises `JwtInvalidError`."""
    with freeze_time("2026-05-07 12:00:00"):
        token = make_token(ttl_seconds=60)

    with freeze_time("2026-05-07 12:05:00"), pytest.raises(JwtInvalidError, match="expired"):
        validator.validate(token)


@pytest.mark.unit
def test_bad_signature_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token signed with a different secret raises `JwtInvalidError`."""
    token = make_token(secret="not-the-real-secret")

    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate(token)


@pytest.mark.unit
def test_wrong_issuer_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token whose `iss` does not match the configured issuer fails."""
    token = make_token(issuer="https://attacker.example")

    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate(token)


@pytest.mark.unit
def test_wrong_audience_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token whose `aud` does not match the configured audience fails."""
    token = make_token(audience="some-other-service")

    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate(token)


@pytest.mark.unit
def test_malformed_token_raises_jwt_invalid_error(validator: JwtValidator) -> None:
    """Garbage that is not a JWT raises `JwtInvalidError`."""
    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate("not-a-real-token")


@pytest.mark.unit
def test_empty_token_raises_jwt_invalid_error(validator: JwtValidator) -> None:
    """An empty string raises `JwtInvalidError`."""
    with pytest.raises(JwtInvalidError, match="invalid token"):
        validator.validate("")


@pytest.mark.unit
def test_missing_sub_claim_raises_jwt_invalid_error(
    validator: JwtValidator,
    make_token: Callable[..., str],
    delete_sentinel: Any,
) -> None:
    """A token missing the required `sub` claim raises `JwtInvalidError`."""
    token = make_token(overrides={"sub": delete_sentinel})

    with pytest.raises(JwtInvalidError, match="invalid token claims"):
        validator.validate(token)


@pytest.mark.unit
def test_empty_sub_claim_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A token whose `sub` is the empty string raises `JwtInvalidError`."""
    token = make_token(overrides={"sub": ""})

    with pytest.raises(JwtInvalidError, match="invalid token claims"):
        validator.validate(token)


@pytest.mark.unit
def test_wrong_typed_claim_raises_jwt_invalid_error(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """A non-list `scopes` claim raises `JwtInvalidError`."""
    token = make_token(overrides={"scopes": "read"})

    with pytest.raises(JwtInvalidError, match="invalid token claims"):
        validator.validate(token)


@pytest.mark.unit
def test_underlying_exception_is_chained(
    validator: JwtValidator, make_token: Callable[..., str]
) -> None:
    """`__cause__` is preserved so tracebacks point at the real failure."""
    token = make_token(secret="not-the-real-secret")

    with pytest.raises(JwtInvalidError) as excinfo:
        validator.validate(token)

    assert excinfo.value.__cause__ is not None
