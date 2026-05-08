"""Ingestion records and lifecycle status.

`IngestionRecord` is the canonical shape stored in DynamoDB by
`ingestion-api` and read by every downstream service (transcription,
summarization). The `IngestionStatus` enum captures the full lifecycle
from upload through summary completion.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.users import USER_ID_PATTERN

# Ten gibibytes. Chosen to bound any single audio/video upload while still
# accepting full-day raw recordings from a high-bitrate device.
MAX_INGESTION_SIZE_BYTES: int = 10 * 1024 * 1024 * 1024

INGESTION_ID_PATTERN = r"^ingest_[a-zA-Z0-9]{16,}$"

IngestionId = Annotated[
    str,
    StringConstraints(pattern=INGESTION_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for ingestion identifiers."""


class IngestionStatus(StrEnum):
    """Lifecycle status of an ingestion record.

    Status transitions are intentionally one-way under normal operation:
    `pending -> uploaded -> transcribing -> transcribed -> summarizing
    -> summarized`. `failed` is a sink; the consuming service is
    responsible for setting `error` alongside.
    """

    PENDING = "pending"
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    SUMMARIZING = "summarizing"
    SUMMARIZED = "summarized"
    FAILED = "failed"


_INGESTION_ID_RE = re.compile(INGESTION_ID_PATTERN)
_USER_ID_RE = re.compile(USER_ID_PATTERN)


class IngestionRecord(BaseDomainModel):
    """The canonical ingestion-record contract.

    Mirrors what `ingestion-api` writes to DynamoDB. Cross-service
    consumers (transcription worker, summarization worker, web UI) read
    this exact shape; treat any field rename as a breaking API change.
    """

    id: str = Field(..., description="Ingestion identifier in the form `ingest_<token>`")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    filename: str = Field(..., min_length=1, description="Caller-supplied original filename")
    content_type: str = Field(..., min_length=1, description="MIME type of the upload")
    size_bytes: int = Field(
        ...,
        gt=0,
        le=MAX_INGESTION_SIZE_BYTES,
        description=f"Upload size in bytes; must be > 0 and <= {MAX_INGESTION_SIZE_BYTES}",
    )
    status: IngestionStatus
    created_at: datetime
    updated_at: datetime
    s3_key: str = Field(..., min_length=1, description="S3 object key holding the raw upload")
    error: str | None = Field(default=None, description="Populated when status is `failed`")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Reject ingestion ids that do not match the documented pattern."""
        if not _INGESTION_ID_RE.match(value):
            raise ValueError(f"id must match pattern {INGESTION_ID_PATTERN!r} (got: {value!r})")
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

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timestamps_utc(cls, value: datetime) -> datetime:
        """Require timezone-aware UTC datetimes on both timestamps."""
        return validate_utc_datetime(value)
