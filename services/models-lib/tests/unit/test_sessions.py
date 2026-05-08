"""Tests for `panakoes_models.sessions`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from panakoes_models.sessions import SessionStatus, StreamingSession

VALID_SESSION: dict[str, object] = {
    "id": "sess_abcd1234efgh5678",
    "user_id": "user_abcdefghijkl",
    "status": SessionStatus.STARTING,
    "gpu_instance_id": None,
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "ttl": None,
}


@pytest.mark.unit
def test_session_constructs() -> None:
    """A valid StreamingSession constructs cleanly."""
    s = StreamingSession(**VALID_SESSION)  # type: ignore[arg-type]
    assert s.id == "sess_abcd1234efgh5678"
    assert s.gpu_instance_id is None
    assert s.ttl is None


@pytest.mark.unit
def test_session_with_gpu_and_ttl_set() -> None:
    """Optional fields populate when supplied."""
    s = StreamingSession(
        **{
            **VALID_SESSION,
            "gpu_instance_id": "i-0123456789abcdef0",
            "ttl": 1_900_000_000,
            "status": SessionStatus.ACTIVE,
        }  # type: ignore[arg-type]
    )
    assert s.gpu_instance_id == "i-0123456789abcdef0"
    assert s.ttl == 1_900_000_000
    assert s.status is SessionStatus.ACTIVE


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "sess_",
        "sess_short",
        "sess_abcd1234efgh567",  # 15 chars (one short)
        "SESS_abcd1234efgh5678",
        "session_abcd1234efgh5678",
    ],
)
def test_session_rejects_invalid_id(bad_id: str) -> None:
    """The session id regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        StreamingSession(**{**VALID_SESSION, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_session_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        StreamingSession(**{**VALID_SESSION, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("status", list(SessionStatus))
def test_session_accepts_every_status(status: SessionStatus) -> None:
    """Every documented status value is accepted."""
    s = StreamingSession(**{**VALID_SESSION, "status": status})  # type: ignore[arg-type]
    assert s.status is status


@pytest.mark.unit
def test_session_rejects_unknown_status() -> None:
    """An undocumented status string is rejected."""
    with pytest.raises(ValidationError):
        StreamingSession(**{**VALID_SESSION, "status": "spinning"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_session_rejects_naive_timestamps() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        StreamingSession(**{**VALID_SESSION, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_session_round_trips_through_json() -> None:
    """JSON round-trip yields an equal session."""
    original = StreamingSession(**VALID_SESSION)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = StreamingSession.model_validate_json(raw)
    assert restored == original
