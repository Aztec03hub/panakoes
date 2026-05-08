"""Tests for `panakoes_models.transcripts`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from panakoes_models.transcripts import Transcript, TranscriptSegment

VALID_TRANSCRIPT: dict[str, object] = {
    "id": "tx_abcd1234efgh5678",
    "ingestion_id": "ingest_abcd1234efgh5678",
    "user_id": "user_abcdefghijkl",
    "text": "Hello world.",
    "language": "en",
    "duration_seconds": 12.5,
    "model": "whisper-large-v3",
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
}


@pytest.mark.unit
def test_transcript_constructs() -> None:
    """A canonical valid Transcript constructs cleanly."""
    t = Transcript(**VALID_TRANSCRIPT)  # type: ignore[arg-type]
    assert t.id == "tx_abcd1234efgh5678"
    assert t.duration_seconds == 12.5
    assert t.language == "en"


@pytest.mark.unit
def test_transcript_optional_fields_default_to_none() -> None:
    """`language` and `duration_seconds` may be omitted."""
    kwargs = {**VALID_TRANSCRIPT}
    kwargs.pop("language")
    kwargs.pop("duration_seconds")
    t = Transcript(**kwargs)  # type: ignore[arg-type]
    assert t.language is None
    assert t.duration_seconds is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "tx_",
        "tx_short",
        "tx_abcd1234efgh567",  # 15 char token (one short)
        "TX_abcd1234efgh5678",
        "trnscr_abcd1234efgh5678",
    ],
)
def test_transcript_rejects_invalid_id(bad_id: str) -> None:
    """The transcript id regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_rejects_invalid_ingestion_id() -> None:
    """A malformed ingestion id is rejected."""
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "ingestion_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_rejects_negative_duration() -> None:
    """Negative `duration_seconds` is rejected."""
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "duration_seconds": -0.1})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_rejects_empty_model() -> None:
    """Empty `model` is rejected."""
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "model": ""})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_rejects_naive_created_at() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        Transcript(**{**VALID_TRANSCRIPT, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_transcript_round_trips_through_json() -> None:
    """JSON round-trip yields an equal transcript."""
    original = Transcript(**VALID_TRANSCRIPT)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = Transcript.model_validate_json(raw)
    assert restored == original


@pytest.mark.unit
def test_transcript_is_frozen() -> None:
    """A constructed transcript cannot be mutated."""
    t = Transcript(**VALID_TRANSCRIPT)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        t.text = "new"  # type: ignore[misc]


@pytest.mark.unit
def test_transcript_segment_constructs() -> None:
    """A valid TranscriptSegment constructs cleanly."""
    seg = TranscriptSegment(start_seconds=0.0, end_seconds=1.5, speaker="A", text="Hi.")
    assert seg.start_seconds == 0.0
    assert seg.end_seconds == 1.5
    assert seg.speaker == "A"


@pytest.mark.unit
def test_transcript_segment_speaker_is_optional() -> None:
    """`speaker` defaults to None when not provided."""
    seg = TranscriptSegment(start_seconds=0.0, end_seconds=1.5, text="Hi.")
    assert seg.speaker is None


@pytest.mark.unit
def test_transcript_segment_rejects_negative_start() -> None:
    """A negative `start_seconds` is rejected."""
    with pytest.raises(ValidationError):
        TranscriptSegment(start_seconds=-0.1, end_seconds=1.0, text="x")


@pytest.mark.unit
def test_transcript_segment_rejects_negative_end() -> None:
    """A negative `end_seconds` is rejected."""
    with pytest.raises(ValidationError):
        TranscriptSegment(start_seconds=0.0, end_seconds=-0.1, text="x")


@pytest.mark.unit
def test_transcript_segment_rejects_end_before_start() -> None:
    """end < start is rejected by the model validator."""
    with pytest.raises(ValidationError):
        TranscriptSegment(start_seconds=2.0, end_seconds=1.0, text="x")


@pytest.mark.unit
def test_transcript_segment_allows_zero_duration() -> None:
    """A zero-duration segment (start == end) is allowed; useful for markers."""
    seg = TranscriptSegment(start_seconds=1.0, end_seconds=1.0, text="")
    assert seg.start_seconds == seg.end_seconds


@pytest.mark.unit
def test_transcript_segment_round_trips_through_json() -> None:
    """JSON round-trip yields an equal segment."""
    original = TranscriptSegment(start_seconds=0.0, end_seconds=1.5, speaker="A", text="Hi.")
    raw = original.model_dump_json()
    restored = TranscriptSegment.model_validate_json(raw)
    assert restored == original
