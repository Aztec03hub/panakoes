"""Tests for `panakoes_models.notifications`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from panakoes_models.notifications import (
    NotificationKind,
    NotificationRecord,
    NotificationStatus,
)

# A valid 26-character Crockford-base32 ULID-shape identifier.
VALID_ULID = "01HV2K8Q3RPN5C9TZWXAEYBJDF"

VALID_NOTIFICATION: dict[str, object] = {
    "id": VALID_ULID,
    "user_id": "user_abcdefghijkl",
    "kind": NotificationKind.EMAIL,
    "status": NotificationStatus.QUEUED,
    "target": "phil@example.com",
    "template": "ingestion-complete",
    "error": None,
    "sent_at": None,
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
}


@pytest.mark.unit
def test_notification_constructs() -> None:
    """A canonical valid NotificationRecord constructs cleanly."""
    n = NotificationRecord(**VALID_NOTIFICATION)  # type: ignore[arg-type]
    assert n.id == VALID_ULID
    assert n.kind is NotificationKind.EMAIL
    assert n.sent_at is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "01HV2K8Q3RPN5C9TZWXAEYBJD",  # 25 chars
        "01HV2K8Q3RPN5C9TZWXAEYBJDF1",  # 27 chars
        "01HV2K8Q3RPN5C9TZWXAEYBjdF",  # lowercase
        "01HV2K8Q3RPN5C9TZWXAEYBLDF",  # contains 'L' (excluded letter)
        "01HV2K8Q3RPN5C9TZWXAEYBJDI",  # contains 'I' (excluded letter)
        "01HV2K8Q3RPN5C9TZWXAEYBJDO",  # contains 'O' (excluded letter)
        "01HV2K8Q3RPN5C9TZWXAEYBJDU",  # contains 'U' (excluded letter)
    ],
)
def test_notification_rejects_invalid_id(bad_id: str) -> None:
    """The Crockford-base32 ULID-shape regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_notification_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("kind", list(NotificationKind))
def test_notification_accepts_every_kind(kind: NotificationKind) -> None:
    """Every documented kind value is accepted."""
    n = NotificationRecord(**{**VALID_NOTIFICATION, "kind": kind})  # type: ignore[arg-type]
    assert n.kind is kind


@pytest.mark.unit
@pytest.mark.parametrize("status", list(NotificationStatus))
def test_notification_accepts_every_status(status: NotificationStatus) -> None:
    """Every documented status value is accepted."""
    n = NotificationRecord(**{**VALID_NOTIFICATION, "status": status})  # type: ignore[arg-type]
    assert n.status is status


@pytest.mark.unit
def test_notification_rejects_unknown_kind() -> None:
    """An undocumented kind is rejected."""
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "kind": "telegram"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("field", ["target", "template"])
def test_notification_rejects_empty_required_strings(field: str) -> None:
    """Empty `target` or `template` fails."""
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, field: ""})  # type: ignore[arg-type]


@pytest.mark.unit
def test_notification_rejects_naive_created_at() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_notification_rejects_naive_sent_at() -> None:
    """A naive `sent_at` is rejected."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "sent_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_notification_rejects_non_utc_sent_at() -> None:
    """A non-UTC `sent_at` is rejected."""
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 5, 7, 12, 0, 0, tzinfo=eastern)
    with pytest.raises(ValidationError):
        NotificationRecord(**{**VALID_NOTIFICATION, "sent_at": aware})  # type: ignore[arg-type]


@pytest.mark.unit
def test_notification_accepts_utc_sent_at() -> None:
    """A timezone-aware UTC `sent_at` is accepted."""
    sent = datetime(2026, 5, 7, 12, 0, 1, tzinfo=UTC)
    n = NotificationRecord(**{**VALID_NOTIFICATION, "sent_at": sent})  # type: ignore[arg-type]
    assert n.sent_at == sent


@pytest.mark.unit
def test_notification_round_trips_through_json() -> None:
    """JSON round-trip yields an equal record."""
    original = NotificationRecord(**VALID_NOTIFICATION)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = NotificationRecord.model_validate_json(raw)
    assert restored == original
