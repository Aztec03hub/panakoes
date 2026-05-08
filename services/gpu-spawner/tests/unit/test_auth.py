"""JWT validation edge cases for `panakoes_gpu_spawner.auth`.

Auth code paths must hit 100% coverage per ADR-018, so every error
branch is exercised here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from jose import jwt

from panakoes_gpu_spawner.auth import (
    AuthenticatedPrincipal,
    _extract_bearer_token,
    get_current_principal,
    require_service_actor,
    verify_jwt,
)
from panakoes_gpu_spawner.config import Settings
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
def test_verify_jwt_accepts_valid_service_token(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """A correctly signed service-actor token yields an `AuthenticatedPrincipal`."""
    token = make_token(
        sub="service:session-manager",
        actor_type="service",
        jti="sess_xyz",
    )
    principal = verify_jwt(token, test_settings)
    assert principal == AuthenticatedPrincipal(
        actor_id="service:session-manager",
        actor_type="service",
        session_id="sess_xyz",
    )


@pytest.mark.unit
def test_verify_jwt_accepts_valid_user_token(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """A correctly signed user-actor token yields a user principal."""
    token = make_token(sub="user_42", actor_type="user", jti="sess_xyz")
    principal = verify_jwt(token, test_settings)
    assert principal.actor_type == "user"
    assert principal.actor_id == "user_42"


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
        "actor_type": "service",
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
        "actor_type": "service",
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
def test_verify_jwt_rejects_missing_actor_type(test_settings: Settings) -> None:
    """A token without `actor_type` is rejected."""
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
def test_verify_jwt_rejects_unknown_actor_type(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """An `actor_type` outside `user|service` is rejected."""
    token = make_token(actor_type="admin")
    with pytest.raises(HTTPException) as exc:
        verify_jwt(token, test_settings)
    assert exc.value.detail == "invalid token claims"


@pytest.mark.unit
def test_verify_jwt_rejects_empty_string_sub(test_settings: Settings) -> None:
    """Empty `sub` claim is rejected."""
    payload = {
        "sub": "",
        "actor_type": "service",
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
def test_verify_jwt_rejects_empty_string_jti(test_settings: Settings) -> None:
    """Empty `jti` claim is rejected."""
    payload = {
        "sub": "u",
        "actor_type": "service",
        "jti": "",
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
    with pytest.raises(HTTPException):
        verify_jwt(token, test_settings)


@pytest.mark.unit
def test_verify_jwt_rejects_non_string_sub(test_settings: Settings) -> None:
    """Numeric `sub` is rejected (must be a string)."""
    payload = {
        "sub": 12345,
        "actor_type": "service",
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
def test_verify_jwt_rejects_non_string_jti(test_settings: Settings) -> None:
    """Non-string `jti` is rejected."""
    payload = {
        "sub": "u",
        "actor_type": "service",
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
def test_get_current_principal_rejects_missing_header(test_settings: Settings) -> None:
    """No Authorization header => 401."""
    request = _FakeRequest(headers={})
    with pytest.raises(HTTPException) as exc:
        get_current_principal(request, test_settings)  # type: ignore[arg-type]
    assert exc.value.status_code == 401
    assert "missing" in exc.value.detail.lower()


@pytest.mark.unit
def test_get_current_principal_rejects_malformed_header(test_settings: Settings) -> None:
    """Non-Bearer scheme => 401."""
    request = _FakeRequest(headers={"authorization": "Basic abc"})
    with pytest.raises(HTTPException):
        get_current_principal(request, test_settings)  # type: ignore[arg-type]


@pytest.mark.unit
def test_get_current_principal_returns_principal_for_valid_header(
    test_settings: Settings,
    make_token: Any,
) -> None:
    """Valid Bearer => `AuthenticatedPrincipal`."""
    token = make_token(sub="user_42", actor_type="user", jti="sess_a")
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    principal = get_current_principal(request, test_settings)  # type: ignore[arg-type]
    assert principal.actor_id == "user_42"
    assert principal.actor_type == "user"


@pytest.mark.unit
def test_require_service_actor_passes_service_token() -> None:
    """A service-actor principal passes through unchanged."""
    principal = AuthenticatedPrincipal(
        actor_id="service:session-manager",
        actor_type="service",
        session_id="sess",
    )
    assert require_service_actor(principal) is principal


@pytest.mark.unit
def test_require_service_actor_rejects_user_token() -> None:
    """A user-actor principal is rejected with 403."""
    principal = AuthenticatedPrincipal(
        actor_id="user_42",
        actor_type="user",
        session_id="sess",
    )
    with pytest.raises(HTTPException) as exc:
        require_service_actor(principal)
    assert exc.value.status_code == 403
