"""Transcript and segment models.

A `Transcript` is produced by the transcription worker for an ingestion
and consumed by the summarizer + the web UI. `TranscriptSegment` is the
fine-grained unit (word-or-phrase chunk with optional speaker label)
that streaming consumers stitch together client-side.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator, model_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.ingestion import INGESTION_ID_PATTERN
from panakoes_models.users import USER_ID_PATTERN

TRANSCRIPT_ID_PATTERN = r"^tx_[a-zA-Z0-9]{16,}$"

TranscriptId = Annotated[
    str,
    StringConstraints(pattern=TRANSCRIPT_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for transcript identifiers."""

_TRANSCRIPT_ID_RE = re.compile(TRANSCRIPT_ID_PATTERN)
_INGESTION_ID_RE = re.compile(INGESTION_ID_PATTERN)
_USER_ID_RE = re.compile(USER_ID_PATTERN)


class Transcript(BaseDomainModel):
    """Full transcript produced for a single ingestion."""

    id: str = Field(..., description="Transcript identifier in the form `tx_<token>`")
    ingestion_id: str = Field(..., description="Source ingestion id; matches `IngestionId`")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    text: str = Field(..., description="Full transcript text; may be empty for silence")
    language: str | None = Field(
        default=None,
        description="BCP-47 language tag if detected; None when detection skipped",
    )
    duration_seconds: float | None = Field(
        default=None,
        ge=0.0,
        description="Audio duration in seconds; None when not yet known",
    )
    model: str = Field(..., min_length=1, description="Transcription model identifier")
    created_at: datetime

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Reject transcript ids that do not match the documented pattern."""
        if not _TRANSCRIPT_ID_RE.match(value):
            raise ValueError(
                f"id must match pattern {TRANSCRIPT_ID_PATTERN!r} (got: {value!r})"
            )
        return value

    @field_validator("ingestion_id")
    @classmethod
    def _validate_ingestion_id(cls, value: str) -> str:
        """Reject ingestion ids that do not match the documented pattern."""
        if not _INGESTION_ID_RE.match(value):
            raise ValueError(
                f"ingestion_id must match pattern {INGESTION_ID_PATTERN!r} (got: {value!r})"
            )
        return value

    @field_validator("user_id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        """Reject user ids that do not match the documented pattern."""
        if not _USER_ID_RE.match(value):
            raise ValueError(
                f"user_id must match pattern {USER_ID_PATTERN!r} (got: {value!r})"
            )
        return value

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_utc(cls, value: datetime) -> datetime:
        """Require `created_at` to be timezone-aware UTC."""
        return validate_utc_datetime(value)


class TranscriptSegment(BaseDomainModel):
    """A single segment within a transcript.

    Streaming consumers receive a sequence of these as the audio is
    processed. The `speaker` label is optional: it is populated only
    when speaker diarization is enabled for the session.
    """

    start_seconds: float = Field(..., ge=0.0, description="Segment start, seconds from 0")
    end_seconds: float = Field(..., ge=0.0, description="Segment end, seconds from 0")
    speaker: str | None = Field(
        default=None,
        description="Diarized speaker label if available",
    )
    text: str = Field(..., description="Segment text; may be empty for non-speech audio")

    @model_validator(mode="after")
    def _validate_time_range(self) -> TranscriptSegment:
        """Require `end_seconds` to be at least `start_seconds`.

        A segment with `end < start` is meaningless and almost certainly
        a programmer error; reject loudly rather than silently store it.
        """
        if self.end_seconds < self.start_seconds:
            raise ValueError(
                "end_seconds must be greater than or equal to start_seconds "
                f"(got start={self.start_seconds}, end={self.end_seconds})"
            )
        return self
