"""Tests for the shared validators / base model in `_common`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from panakoes_models._common import BaseDomainModel, validate_utc_datetime


@pytest.mark.unit
def test_validate_utc_datetime_accepts_utc() -> None:
    """A timezone-aware UTC datetime passes through unchanged."""
    aware = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    assert validate_utc_datetime(aware) is aware


@pytest.mark.unit
def test_validate_utc_datetime_rejects_naive() -> None:
    """A naive datetime is rejected with a clear message."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_utc_datetime(naive)


@pytest.mark.unit
def test_validate_utc_datetime_rejects_non_utc() -> None:
    """A non-UTC offset is rejected even if timezone-aware."""
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 5, 7, 12, 0, 0, tzinfo=eastern)
    with pytest.raises(ValueError, match="UTC"):
        validate_utc_datetime(aware)


@pytest.mark.unit
def test_base_domain_model_is_frozen() -> None:
    """`BaseDomainModel` instances refuse attribute mutation."""

    class Thing(BaseDomainModel):
        name: str

    t = Thing(name="x")
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError on frozen
        t.name = "y"  # type: ignore[misc]


@pytest.mark.unit
def test_base_domain_model_strips_whitespace() -> None:
    """`BaseDomainModel` strips surrounding whitespace from string fields."""

    class Thing(BaseDomainModel):
        name: str

    assert Thing(name="  hi  ").name == "hi"


@pytest.mark.unit
def test_base_domain_model_forbids_extra_fields() -> None:
    """`BaseDomainModel` rejects unknown fields with `extra='forbid'`."""

    class Thing(BaseDomainModel):
        name: str

    with pytest.raises(Exception):  # noqa: B017 - ValidationError; full type check below
        Thing(name="ok", surprise="boom")  # type: ignore[call-arg]
