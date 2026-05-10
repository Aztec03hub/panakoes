"""Pydantic request and response models for the Ingestion API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IngestionStatus = Literal["pending", "uploaded", "failed"]
TranscriptStatus = Literal["pending", "succeeded", "failed"]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class TranscriptWordModel(BaseModel):
    """A single word with its time interval inside the audio."""

    text: str
    start: float
    end: float


class TranscriptSegmentModel(BaseModel):
    """A sub-utterance produced by a Whisper-family model."""

    text: str
    start: float
    end: float
    words: list[TranscriptWordModel] = Field(default_factory=list)


class TranscriptModel(BaseModel):
    """Pydantic mirror of `panakoes_transcriber.TranscriptionResult`.

    We mirror the shared dataclass into a Pydantic model so it round-
    trips cleanly through FastAPI's JSON serializer and so DynamoDB
    persistence can use `model_dump()` without bespoke converters.
    """

    text: str
    segments: list[TranscriptSegmentModel] = Field(default_factory=list)
    language: str | None = None
    duration_seconds: float | None = None


class CreateIngestionRequest(BaseModel):
    """Request body for `POST /ingestion/audio`.

    Bounds on `size_bytes` reject obviously-bogus values without
    encoding a specific business rule here; the actual content-length
    enforcement happens in the pre-signed URL conditions.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    filename: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(..., min_length=1, max_length=128)
    size_bytes: int = Field(..., ge=1, le=10 * 1024 * 1024 * 1024)


class CreateIngestionResponse(BaseModel):
    """Response body for `POST /ingestion/audio`."""

    ingestion_id: str
    upload_url: str
    expires_at: datetime


class IngestionRecord(BaseModel):
    """Full ingestion record as stored in DynamoDB."""

    model_config = ConfigDict(extra="ignore")

    ingestion_id: str
    user_id: str
    filename: str
    content_type: str
    size_bytes: int
    s3_key: str
    status: IngestionStatus
    created_at: datetime
    updated_at: datetime
    transcript_status: TranscriptStatus | None = None
    transcript: TranscriptModel | None = None
    transcript_error_message: str | None = None


class IngestionListResponse(BaseModel):
    """Response body for `GET /ingestion`."""

    items: list[IngestionRecord]
    next_cursor: str | None = None
