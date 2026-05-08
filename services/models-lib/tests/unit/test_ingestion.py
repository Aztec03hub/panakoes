"""Tests for `panakoes_models.ingestion`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from panakoes_models.ingestion import (
    MAX_INGESTION_SIZE_BYTES,
    IngestionRecord,
    IngestionStatus,
)

VALID_INGESTION: dict[str, object] = {
    "id": "ingest_abcd1234efgh5678",
    "user_id": "user_abcdefghijkl",
    "filename": "meeting.mp3",
    "content_type": "audio/mpeg",
    "size_bytes": 4_200_000,
    "status": IngestionStatus.PENDING,
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "updated_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "s3_key": "ingestions/user_abcdefghijkl/ingest_abcd1234efgh5678.mp3",
    "error": None,
}


@pytest.mark.unit
def test_ingestion_record_constructs() -> None:
    """A valid IngestionRecord constructs cleanly."""
    rec = IngestionRecord(**VALID_INGESTION)  # type: ignore[arg-type]
    assert rec.id == "ingest_abcd1234efgh5678"
    assert rec.status is IngestionStatus.PENDING
    assert rec.error is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "ingest_",
        "ingest_short",
        "ingest_abcd1234efgh567",  # 15 char token (one short)
        "INGEST_abcd1234efgh5678",
        "ingestion_abcd1234efgh5678",
        "ingest-abcd1234efgh5678",
    ],
)
def test_ingestion_record_rejects_invalid_id(bad_id: str) -> None:
    """The ingest id regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("field", ["filename", "content_type", "s3_key"])
def test_ingestion_record_rejects_empty_required_strings(field: str) -> None:
    """Empty strings on required fields raise validation errors."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, field: ""})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_rejects_zero_size() -> None:
    """size_bytes must be strictly positive."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "size_bytes": 0})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_rejects_negative_size() -> None:
    """size_bytes cannot be negative."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "size_bytes": -1})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_accepts_max_size() -> None:
    """size_bytes at exactly the 10 GiB cap is accepted."""
    rec = IngestionRecord(
        **{**VALID_INGESTION, "size_bytes": MAX_INGESTION_SIZE_BYTES}  # type: ignore[arg-type]
    )
    assert rec.size_bytes == MAX_INGESTION_SIZE_BYTES


@pytest.mark.unit
def test_ingestion_record_rejects_size_above_cap() -> None:
    """size_bytes above the 10 GiB cap is rejected."""
    with pytest.raises(ValidationError):
        IngestionRecord(
            **{**VALID_INGESTION, "size_bytes": MAX_INGESTION_SIZE_BYTES + 1}  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_ingestion_record_rejects_naive_timestamps() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_rejects_non_utc_timestamps() -> None:
    """Non-UTC `updated_at` raises a validation error."""
    eastern = timezone(timedelta(hours=-5))
    aware = datetime(2026, 5, 7, 12, 0, 0, tzinfo=eastern)
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "updated_at": aware})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("status", list(IngestionStatus))
def test_ingestion_record_accepts_every_status(status: IngestionStatus) -> None:
    """Every documented status value is accepted."""
    rec = IngestionRecord(**{**VALID_INGESTION, "status": status})  # type: ignore[arg-type]
    assert rec.status is status


@pytest.mark.unit
def test_ingestion_record_rejects_unknown_status_string() -> None:
    """An undocumented status string is rejected."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "status": "exploded"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_rejects_extra_fields() -> None:
    """`extra='forbid'` rejects unknown keys."""
    with pytest.raises(ValidationError):
        IngestionRecord(**{**VALID_INGESTION, "rogue": True})  # type: ignore[arg-type]


@pytest.mark.unit
def test_ingestion_record_is_frozen() -> None:
    """A constructed record cannot be mutated."""
    rec = IngestionRecord(**VALID_INGESTION)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        rec.status = IngestionStatus.UPLOADED  # type: ignore[misc]


@pytest.mark.unit
def test_ingestion_record_round_trips_through_json() -> None:
    """JSON serialize -> deserialize round-trips to an equal record."""
    original = IngestionRecord(**VALID_INGESTION)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = IngestionRecord.model_validate_json(raw)
    assert restored == original
