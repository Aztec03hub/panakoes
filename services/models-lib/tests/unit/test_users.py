"""Tests for `panakoes_models.users`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from panakoes_models.users import User

VALID_USER: dict[str, object] = {
    "id": "user_abcdefghijkl",
    "email": "phil@example.com",
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "role": "user",
    "tier": "free",
}


@pytest.mark.unit
def test_user_constructs_with_valid_fields() -> None:
    """A canonical valid User constructs and exposes the expected fields."""
    u = User(**VALID_USER)  # type: ignore[arg-type]
    assert u.id == "user_abcdefghijkl"
    assert u.email == "phil@example.com"
    assert u.role == "user"
    assert u.tier == "free"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "user_",  # empty token
        "user_short",  # 5 chars
        "user_abcdefghijk",  # 11 chars (one short of 12)
        "USER_abcdefghijkl",  # wrong prefix case
        "u_abcdefghijkl",  # wrong prefix
        "user-abcdefghijkl",  # hyphen
        "user_abcdefghijkl!",  # invalid char
    ],
)
def test_user_rejects_invalid_id(bad_id: str) -> None:
    """`UserId` regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_user_rejects_invalid_email() -> None:
    """`EmailStr` rejects malformed addresses."""
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "email": "not-an-email"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_user_rejects_naive_created_at() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_user_rejects_non_utc_created_at() -> None:
    """A non-UTC `created_at` raises a validation error."""
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 5, 7, 12, 0, 0, tzinfo=eastern)
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "created_at": aware})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("role", ["user", "admin"])
def test_user_accepts_documented_roles(role: str) -> None:
    """Both documented role literals are accepted."""
    u = User(**{**VALID_USER, "role": role})  # type: ignore[arg-type]
    assert u.role == role


@pytest.mark.unit
def test_user_rejects_unknown_role() -> None:
    """An undocumented role is rejected."""
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "role": "superadmin"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["free", "pro", "team"])
def test_user_accepts_documented_tiers(tier: str) -> None:
    """All documented tier literals are accepted."""
    u = User(**{**VALID_USER, "tier": tier})  # type: ignore[arg-type]
    assert u.tier == tier


@pytest.mark.unit
def test_user_rejects_unknown_tier() -> None:
    """An undocumented tier is rejected."""
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "tier": "enterprise"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_user_rejects_extra_field() -> None:
    """`extra='forbid'` rejects unknown keys."""
    with pytest.raises(ValidationError):
        User(**{**VALID_USER, "extra": "field"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_user_is_frozen() -> None:
    """A constructed user cannot be mutated."""
    u = User(**VALID_USER)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        u.role = "admin"  # type: ignore[misc]


@pytest.mark.unit
def test_user_round_trips_through_json() -> None:
    """`model_dump_json` -> `model_validate_json` returns an equivalent user."""
    original = User(**VALID_USER)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = User.model_validate_json(raw)
    assert restored == original
