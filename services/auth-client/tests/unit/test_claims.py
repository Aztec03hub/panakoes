"""Tests for the `JwtClaims` Pydantic model.

The validator already exercises the model end-to-end; this file
verifies the model's intrinsic behavior so it's clear which
guarantees come from the model itself versus the validator wrapper.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_auth_client import JwtClaims


@pytest.mark.unit
def test_claims_construct_from_minimum_fields() -> None:
    """Required fields populated and optional fields default cleanly."""
    claims = JwtClaims(
        sub="user_abc",
        iss="https://auth.example",
        aud="api",
        iat=1_700_000_000,
        exp=1_700_000_300,
    )

    assert claims.sub == "user_abc"
    assert claims.jti is None
    assert claims.scopes == []


@pytest.mark.unit
def test_claims_drop_extra_fields() -> None:
    """`extra='ignore'` keeps the model surface stable across token versions."""
    claims = JwtClaims(
        sub="user_abc",
        iss="https://auth.example",
        aud="api",
        iat=1,
        exp=2,
        custom_undocumented_field="present",  # type: ignore[call-arg]
    )

    assert not hasattr(claims, "custom_undocumented_field")


@pytest.mark.unit
def test_claims_are_frozen() -> None:
    """The model is frozen so handlers cannot mutate the validated payload."""
    claims = JwtClaims(
        sub="user_abc",
        iss="https://auth.example",
        aud="api",
        iat=1,
        exp=2,
    )

    with pytest.raises(ValidationError):
        claims.sub = "user_other"  # type: ignore[misc]


@pytest.mark.unit
def test_empty_sub_is_rejected() -> None:
    """`sub` must be non-empty (it identifies the user)."""
    with pytest.raises(ValidationError):
        JwtClaims(
            sub="",
            iss="https://auth.example",
            aud="api",
            iat=1,
            exp=2,
        )


@pytest.mark.unit
def test_scopes_list_is_preserved() -> None:
    """A list of scope strings is preserved on the model."""
    claims = JwtClaims(
        sub="user_abc",
        iss="https://auth.example",
        aud="api",
        iat=1,
        exp=2,
        scopes=["read", "write", "admin"],
    )

    assert claims.scopes == ["read", "write", "admin"]
