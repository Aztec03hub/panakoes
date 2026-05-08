"""JWT validation edge cases for `panakoes_session_manager.auth`.

Auth code paths must hit 100% coverage per ADR-018, so every error
branch is exercised here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from jose import jwt

from panakoes_session_manager.auth import (
    AuthenticatedUser,
    _extract_bearer_token,
    _settings,
    get_current_user,
    verify_jwt,
)
from panakoes_session_manager.config import Settings
from tests.conftest import (
    TEST_JWT_AUDIENCE,
    TEST_JWT_ISSUER,
    TEST_JWT_SECRET,
)


class _FakeRequest:
    """Minimal stand-in for `fastapi.Request` exposing only `.headers`."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


@pytest.mark.unit
def test_extract_bearer_token_handles_missing_header() -> None:
    """No header returns `None`."""
    assert _extract_bearer_token(None) is None
    assert _extract_bearer_token("") is None


@pytest.mark.unit
def test_extract_bearer_token_rejects_non_bearer_scheme() -> None:
    """A non-Bearer scheme is treated as malformed."""
    assert _extract_bearer_token("Basic dXNlcjpwYXNz") is None
    assert _extract_bearer_token("Token abc.def.ghi") is None


@pytest.mark.unit
def test_extract_bearer_token_returns_token_for_valid_header() -> None:
    """`Bearer <token>` with mixed case still parses correctly."""
    assert _extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert _extract_bearer_token("bearer abc.def.ghi") == "abc.def.ghi"
    assert _extract_bearer_token("  Bearer   abc.def.ghi  ") == "abc.def.ghi"


@pytest.mark.unit
def test_extract_bearer_token_rejects_empty_token_after_bearer() -> None:
    """`Bearer ` with no token is malformed."""
    assert _extract_bearer_token("Bearer ") is None
    assert _extract_bearer_token("Bearer    ") is None


@pytest.mark.unit
def test_settings_dependency_returns_settings_instance() -> None:
    """The lru-cached dependency yields a `Settings` (covers the helper)."""
    _settings.cache_clear()
    result = _settings()
    assert isinstance(result, Settings)
    # Calling again returns the same cached instance.
    assert _settings() is result
    _settings.cache_clear()


@pytest.mark.unit
def test_verify_jwt_accepts_valid_token(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """A correctly signed token with valid claims yields an `AuthenticatedUser`."""
    token = make_token(sub="user_42", email="alice@example.com", jti="sess_xyz")
    user = verify_jwt(token, test_settings)
    assert user == AuthenticatedUser(
        user_id="user_42", email="alice@example.com", session_id="sess_xyz"
    )


@pytest.mark.unit
def test_verify_jwt_rejects_expired_token(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """An expired token raises 401."""
    token = make_token(
        issued_at=datetime.now(UTC) - timedelta(hours=2),
        expires_delta=timedelta(hours=1),
    )
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.status_code == 401
    assert exc.value.detail == "token expired"


@pytest.mark.unit
def test_verify_jwt_rejects_bad_signature(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """A token signed with a different secret raises 401."""
    token = make_token(secret="totally-different-secret-32-chars!")
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid token"


@pytest.mark.unit
def test_verify_jwt_rejects_wrong_issuer(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """Issuer mismatch is rejected."""
    token = make_token(issuer="https://attacker.example.com")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_wrong_audience(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """Audience mismatch is rejected."""
    token = make_token(audience="some-other-api")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_malformed_token(test_settings: Settings) -> None:
    """A garbage string is rejected."""
    with pytest.raises(HTTPException) as exc:
        verify_jwt("not-a-real-jwt", test_settings)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_jwt_rejects_missing_sub_claim(test_settings: Settings) -> None:
    """A token without `sub` is rejected."""
    payload = {
        "email": "a@b.com",
        "jti": "j",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.detail == "invalid token claims"


@pytest.mark.unit
def test_verify_jwt_rejects_missing_email_claim(test_settings: Settings) -> None:
    """A token without `email` is rejected."""
    payload = {
        "sub": "u",
        "jti": "j",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.detail == "invalid token claims"


@pytest.mark.unit
def test_verify_jwt_rejects_missing_jti_claim(test_settings: Settings) -> None:
    """A token without `jti` is rejected."""
    payload = {
        "sub": "u",
        "email": "a@b.com",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.detail == "invalid token claims"


@pytest.mark.unit
def test_verify_jwt_rejects_empty_string_claims(test_settings: Settings) -> None:
    """Empty-string claims are rejected (treated as missing)."""
    payload = {
        "sub": "",
        "email": "a@b.com",
        "jti": "j",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_non_string_claim_types(test_settings: Settings) -> None:
    """Numeric `sub` (or similar) is rejected (must be a string)."""
    payload = {
        "sub": 12345,
        "email": "a@b.com",
        "jti": "j",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_non_string_email_claim(test_settings: Settings) -> None:
    """Non-string `email` is rejected."""
    payload = {
        "sub": "u",
        "email": 42,
        "jti": "j",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_non_string_jti_claim(test_settings: Settings) -> None:
    """Non-string `jti` is rejected."""
    payload = {
        "sub": "u",
        "email": "a@b.com",
        "jti": 99,
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_get_current_user_rejects_missing_header(test_settings: Settings) -> None:
    """No Authorization header => 401."""
    request = _FakeRequest(headers={})
    with pytest.raises(HTTPException) as exc:
        get_current_user(request, test_settings)  # type: ignore[arg-type]
    assert exc.value.status_code == 401
    assert "missing" in exc.value.detail.lower()


@pytest.mark.unit
def test_get_current_user_rejects_malformed_header(test_settings: Settings) -> None:
    """Non-Bearer scheme => 401."""
    request = _FakeRequest(headers={"authorization": "Basic abc"})
    with pytest.raises(HTTPException):
        get_current_user(request, test_settings)  # type: ignore[arg-type]


@pytest.mark.unit
def test_get_current_user_returns_user_for_valid_header(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """Valid Bearer => `AuthenticatedUser`."""
    token = make_token(sub="user_42", email="x@y.z", jti="sess_a")
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    user = get_current_user(request, test_settings)  # type: ignore[arg-type]
    assert user.user_id == "user_42"
