"""Pydantic request and response models for the Query API.

The shapes mirror what the producing services persist:

- `IngestionRecord` matches `services/ingestion-api/models.py` exactly,
  so a Query API client can deserialise an ingestion the same way the
  Ingestion API does.
- `SummaryRecord` describes the row the (still-being-built)
  Summarization service writes; the schema is a forward contract this
  service publishes for that producer to follow.
- `SessionRecord` describes the row the Session Manager writes for
  streaming sessions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

IngestionStatus = Literal["pending", "uploaded", "failed"]
TranscriptStatus = Literal["pending", "succeeded", "failed"]
SummaryTier = Literal["fast", "deep"]
SessionStatus = Literal["starting", "active", "ended", "failed"]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class TranscriptWordModel(BaseModel):
    """Mirror of `ingestion_api.models.TranscriptWordModel`."""

    text: str
    start: float
    end: float


class TranscriptSegmentModel(BaseModel):
    """Mirror of `ingestion_api.models.TranscriptSegmentModel`."""

    text: str
    start: float
    end: float
    words: list[TranscriptWordModel] = []


class TranscriptModel(BaseModel):
    """Mirror of `ingestion_api.models.TranscriptModel`."""

    model_config = ConfigDict(extra="ignore")

    text: str
    language: str | None = None
    duration_seconds: float | None = None
    word_count: int | None = None
    segments: list[TranscriptSegmentModel] = []


class IngestionRecord(BaseModel):
    """Full ingestion record as stored in DynamoDB.

    Mirror of `services/ingestion-api/.../models.py:IngestionRecord` plus
    the transcript-related fields the transcribe-worker / transcriber-batch
    write after a job lands. The query-api response includes the transcript
    when present so the SPA's `/ingestion/[id]` polling view can render it.
    """

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
    """Response body for `GET /ingestions`."""

    items: list[IngestionRecord]
    next_cursor: str | None = None


class SummaryRecord(BaseModel):
    """Summary record produced by the Summarization service.

    `tier` distinguishes free-tier "fast" Haiku-4.5 summaries from
    paid-tier "deep" Sonnet-4.6 summaries (see PLANNING.md). The
    `model` string captures the exact Anthropic model id used so we
    have an audit trail when we later change defaults.
    """

    model_config = ConfigDict(extra="ignore")

    transcript_id: str
    user_id: str
    summary_text: str
    tier: SummaryTier
    model: str
    created_at: datetime
    updated_at: datetime


class SummaryListResponse(BaseModel):
    """Response body for `GET /summaries`."""

    items: list[SummaryRecord]
    next_cursor: str | None = None


class SessionRecord(BaseModel):
    """Streaming-session record as written by the Session Manager."""

    model_config = ConfigDict(extra="ignore")

    session_id: str
    user_id: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None


class SessionListResponse(BaseModel):
    """Response body for `GET /sessions`."""

    items: list[SessionRecord]
    next_cursor: str | None = None
