"""Unit tests for `panakoes_test_helpers.jwt`."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from panakoes_test_helpers.jwt import bearer_header, make_expired_token, make_test_token


@pytest.mark.unit
def test_make_test_token_returns_decodable_hs256_jwt() -> None:
    """A token built with the defaults round-trips through HS256 decode."""
    token = make_test_token("user_abc")
    payload = pyjwt.decode(
        token,
        "test-secret",
        algorithms=["HS256"],
        audience="panakoes-api",
        issuer="panakoes-auth",
    )
    assert payload["sub"] == "user_abc"
    assert payload["iss"] == "panakoes-auth"
    assert payload["aud"] == "panakoes-api"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]
    assert "scopes" not in payload


@pytest.mark.unit
def test_make_test_token_default_validity_is_one_hour() -> None:
    """`expires_in_seconds` defaults to 3600 (one hour)."""
    token = make_test_token("user_abc")
    payload = pyjwt.decode(
        token, "test-secret", algorithms=["HS256"], audience="panakoes-api"
    )
    assert payload["exp"] - payload["iat"] == 3600


@pytest.mark.unit
def test_make_test_token_includes_scopes_when_provided() -> None:
    """A non-empty `scopes` list lands as the `scopes` claim."""
    token = make_test_token("user_abc", scopes=["read:transcripts", "write:summaries"])
    payload = pyjwt.decode(
        token, "test-secret", algorithms=["HS256"], audience="panakoes-api"
    )
    assert payload["scopes"] == ["read:transcripts", "write:summaries"]


@pytest.mark.unit
def test_make_test_token_omits_scopes_when_none_passed() -> None:
    """`scopes=None` means no `scopes` claim is set (keeps payload minimal)."""
    token = make_test_token("user_abc", scopes=None)
    payload = pyjwt.decode(
        token, "test-secret", algorithms=["HS256"], audience="panakoes-api"
    )
    assert "scopes" not in payload


@pytest.mark.unit
def test_make_test_token_respects_custom_secret_issuer_audience() -> None:
    """All overrides flow through to the encoded payload."""
    token = make_test_token(
        "user_xyz",
        secret="rotated-secret",
        issuer="auth.panakoes.test",
        audience="panakoes-api-test",
        expires_in_seconds=120,
    )
    payload = pyjwt.decode(
        token,
        "rotated-secret",
        algorithms=["HS256"],
        audience="panakoes-api-test",
        issuer="auth.panakoes.test",
    )
    assert payload["sub"] == "user_xyz"
    assert payload["iss"] == "auth.panakoes.test"
    assert payload["aud"] == "panakoes-api-test"
    assert payload["exp"] - payload["iat"] == 120


@pytest.mark.unit
def test_make_test_token_rejects_empty_subject() -> None:
    """An empty `sub` would silently authenticate as "no user"; reject it."""
    with pytest.raises(ValueError, match="sub"):
        make_test_token("")


@pytest.mark.unit
def test_make_test_token_rejects_zero_or_negative_expiry() -> None:
    """Use `make_expired_token` for stale tokens; positive only here."""
    with pytest.raises(ValueError, match="expires_in_seconds"):
        make_test_token("user_abc", expires_in_seconds=0)
    with pytest.raises(ValueError, match="expires_in_seconds"):
        make_test_token("user_abc", expires_in_seconds=-30)


@pytest.mark.unit
def test_make_expired_token_has_exp_in_the_past() -> None:
    """The token's `exp` claim is strictly less than `now`."""
    token = make_expired_token("user_abc", expired_seconds_ago=120)
    # Decoding without `verify_exp` so we can inspect the stale claim.
    payload = pyjwt.decode(
        token,
        "test-secret",
        algorithms=["HS256"],
        audience="panakoes-api",
        options={"verify_exp": False},
    )
    assert payload["exp"] < time.time()


@pytest.mark.unit
def test_make_expired_token_decode_raises_on_default_verify() -> None:
    """A standard decode (with exp verification on) rejects the token."""
    token = make_expired_token("user_abc")
    with pytest.raises(pyjwt.ExpiredSignatureError):
        pyjwt.decode(
            token, "test-secret", algorithms=["HS256"], audience="panakoes-api"
        )


@pytest.mark.unit
def test_make_expired_token_includes_scopes_when_provided() -> None:
    """`scopes` are preserved in expired tokens too (for testing scope-then-expiry paths)."""
    token = make_expired_token("user_abc", scopes=["admin"])
    payload = pyjwt.decode(
        token,
        "test-secret",
        algorithms=["HS256"],
        audience="panakoes-api",
        options={"verify_exp": False},
    )
    assert payload["scopes"] == ["admin"]


@pytest.mark.unit
def test_make_expired_token_rejects_empty_subject() -> None:
    """Same `sub` discipline as `make_test_token`."""
    with pytest.raises(ValueError, match="sub"):
        make_expired_token("")


@pytest.mark.unit
def test_make_expired_token_rejects_non_positive_offset() -> None:
    """`expired_seconds_ago` must be positive (use the real path otherwise)."""
    with pytest.raises(ValueError, match="expired_seconds_ago"):
        make_expired_token("user_abc", expired_seconds_ago=0)


@pytest.mark.unit
def test_bearer_header_returns_canonical_shape() -> None:
    """The dict carries exactly one Authorization header, formatted Bearer-style."""
    header = bearer_header("abc.def.ghi")
    assert header == {"Authorization": "Bearer abc.def.ghi"}


@pytest.mark.unit
def test_bearer_header_rejects_empty_token() -> None:
    """An empty token would produce `Bearer `, which silently passes some parsers."""
    with pytest.raises(ValueError, match="token"):
        bearer_header("")


@pytest.mark.unit
def test_bearer_header_round_trips_with_make_test_token() -> None:
    """Self-test: builders compose end-to-end as documented."""
    token = make_test_token("user_abc")
    header = bearer_header(token)
    assert header["Authorization"].startswith("Bearer ")
    raw = header["Authorization"].removeprefix("Bearer ")
    payload = pyjwt.decode(
        raw, "test-secret", algorithms=["HS256"], audience="panakoes-api"
    )
    assert payload["sub"] == "user_abc"
