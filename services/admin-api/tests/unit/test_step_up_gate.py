"""Unit tests for the step-up MFA gate dependency.

Tests the gate in isolation by calling `require_admin_with_step_up`
directly against a hand-constructed `JwtClaims`. Avoids spinning up
FastAPI for what is fundamentally a pure-function check.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException
from panakoes_auth_client import JwtClaims

from panakoes_admin_api.auth import (
    DEFAULT_STEP_UP_MAX_AGE_SECONDS,
    require_admin,
    require_admin_with_step_up,
    step_up_max_age_seconds,
)


def _admin_claims_at(step_up_at: int | None) -> JwtClaims:
    return JwtClaims(
        sub="user_admin",
        iss="https://auth.example",
        aud="admin-api",
        iat=1,
        exp=2_000_000_000,
        role="admin",
        mfa_step_up_at=step_up_at,
    )


@pytest.mark.unit
def test_step_up_gate_rejects_request_without_step_up_claim() -> None:
    """Admin token without `mfa_step_up_at` is rejected with 403."""
    claims = _admin_claims_at(None)
    with pytest.raises(HTTPException) as exc:
        require_admin_with_step_up(claims=claims)
    assert exc.value.status_code == 403
    assert "step-up" in exc.value.detail.lower()


@pytest.mark.unit
def test_step_up_gate_rejects_step_up_claim_older_than_window() -> None:
    """Step-up timestamp 6 minutes old (default window 5 min) is rejected with 403."""
    six_minutes_ago = int(time.time()) - 360
    claims = _admin_claims_at(six_minutes_ago)
    with pytest.raises(HTTPException) as exc:
        require_admin_with_step_up(claims=claims)
    assert exc.value.status_code == 403
    assert "too old" in exc.value.detail.lower()


@pytest.mark.unit
def test_step_up_gate_accepts_step_up_claim_within_window() -> None:
    """Step-up timestamp 4 minutes old is within the default 5-minute window."""
    four_minutes_ago = int(time.time()) - 240
    claims = _admin_claims_at(four_minutes_ago)
    result = require_admin_with_step_up(claims=claims)
    assert result is claims


@pytest.mark.unit
def test_step_up_max_age_falls_back_on_unset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`step_up_max_age_seconds()` returns the default when env var is unset."""
    monkeypatch.delenv("STEP_UP_MAX_AGE_SECONDS", raising=False)
    assert step_up_max_age_seconds() == DEFAULT_STEP_UP_MAX_AGE_SECONDS


@pytest.mark.unit
def test_step_up_max_age_falls_back_on_garbage_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-int env value falls back to the default rather than raising."""
    monkeypatch.setenv("STEP_UP_MAX_AGE_SECONDS", "not-a-number")
    assert step_up_max_age_seconds() == DEFAULT_STEP_UP_MAX_AGE_SECONDS


@pytest.mark.unit
def test_step_up_max_age_falls_back_on_non_positive_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero or negative env value falls back to the default."""
    monkeypatch.setenv("STEP_UP_MAX_AGE_SECONDS", "0")
    assert step_up_max_age_seconds() == DEFAULT_STEP_UP_MAX_AGE_SECONDS
    monkeypatch.setenv("STEP_UP_MAX_AGE_SECONDS", "-1")
    assert step_up_max_age_seconds() == DEFAULT_STEP_UP_MAX_AGE_SECONDS


@pytest.mark.unit
def test_step_up_max_age_honors_valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid positive int env value is honored."""
    monkeypatch.setenv("STEP_UP_MAX_AGE_SECONDS", "60")
    assert step_up_max_age_seconds() == 60


@pytest.mark.unit
def test_require_admin_rejects_non_admin() -> None:
    """`role != 'admin'` returns 403 from the upstream `require_admin`."""
    claims = JwtClaims(
        sub="user_regular",
        iss="https://auth.example",
        aud="admin-api",
        iat=1,
        exp=2_000_000_000,
        role="user",
    )
    with pytest.raises(HTTPException) as exc:
        require_admin(claims=claims)
    assert exc.value.status_code == 403
