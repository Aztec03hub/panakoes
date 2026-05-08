"""Tests for `auth.py`. Coverage on this module is held to 100% per ADR-018."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from panakoes_summarization import auth
from panakoes_summarization.config import Settings
from tests.conftest import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
    make_token,
)


@pytest.fixture
def settings(env: None) -> Settings:
    """Return `Settings()` populated from the standard test env."""
    _ = env
    return Settings()


@pytest.mark.unit
def test_get_settings_returns_settings_instance(env: None) -> None:
    """The dependency factory yields an isolated `Settings` instance."""
    _ = env
    s = auth.get_settings()
    assert isinstance(s, Settings)
    assert s.jwt_secret == JWT_SECRET


@pytest.mark.unit
def test_returns_subject_for_valid_bearer_token(settings: Settings) -> None:
    """A correctly-signed token returns its `sub` claim."""
    token = make_token(sub="user_42")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert auth.get_current_user_id(credentials=creds, settings=settings) == "user_42"


@pytest.mark.unit
def test_lowercase_bearer_scheme_is_accepted(settings: Settings) -> None:
    """The scheme comparison is case-insensitive."""
    token = make_token()
    creds = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)
    user_id = auth.get_current_user_id(credentials=creds, settings=settings)
    assert user_id


@pytest.mark.unit
def test_missing_credentials_raise_401(settings: Settings) -> None:
    """No `Authorization` header at all is a 401."""
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=None, settings=settings)
    assert info.value.status_code == 401
    assert info.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.unit
def test_non_bearer_scheme_raises_401(settings: Settings) -> None:
    """A `Basic ...` header is rejected even if the credentials parse."""
    creds = HTTPAuthorizationCredentials(scheme="Basic", credentials="abc123")
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_invalid_signature_raises_401(settings: Settings) -> None:
    """A token signed with a different secret is rejected."""
    token = make_token(secret="some-other-secret-of-sufficient-length-xx")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_expired_token_raises_401(settings: Settings) -> None:
    """A token whose `exp` is in the past is rejected."""
    token = make_token(expires_in=-60)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_wrong_issuer_raises_401(settings: Settings) -> None:
    """A token issued by another service is rejected."""
    token = make_token(issuer="someone-else")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_wrong_audience_raises_401(settings: Settings) -> None:
    """A token issued for a different audience is rejected."""
    token = make_token(audience="some-other-audience")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_missing_required_claim_raises_401(settings: Settings) -> None:
    """A token missing a required claim (e.g. `exp`) is rejected."""
    now = datetime.now(UTC)
    payload = {
        "sub": "u1",
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        # exp deliberately omitted
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_empty_subject_raises_401(settings: Settings) -> None:
    """A token whose `sub` claim is an empty string is rejected."""
    now = datetime.now(UTC)
    payload = {
        "sub": "",
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_non_string_subject_raises_401(settings: Settings) -> None:
    """A token whose `sub` is not a string (e.g. an int) is rejected."""
    now = datetime.now(UTC)
    payload = {
        "sub": 12345,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401


@pytest.mark.unit
def test_unconfigured_secret_refuses_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """If `JWT_SECRET` is empty, every request is rejected."""
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", JWT_AUDIENCE)
    settings = Settings()
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="some-token")
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401
    assert info.value.detail == "authentication is not configured"


@pytest.mark.unit
def test_garbage_token_raises_401(settings: Settings) -> None:
    """A non-JWT credential string is rejected."""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")
    with pytest.raises(HTTPException) as info:
        auth.get_current_user_id(credentials=creds, settings=settings)
    assert info.value.status_code == 401
