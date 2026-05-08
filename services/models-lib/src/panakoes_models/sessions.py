"""Streaming-session models.

A `StreamingSession` represents a live transcription session attached
to a session-spawned GPU instance. Tracked end-to-end so the session
manager can scrub orphaned GPU spot instances when a client drops.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.users import USER_ID_PATTERN

SESSION_ID_PATTERN = r"^sess_[a-zA-Z0-9]{16,}$"

SessionId = Annotated[
    str,
    StringConstraints(pattern=SESSION_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for streaming-session identifiers."""


class SessionStatus(StrEnum):
    """Lifecycle status of a streaming session."""

    STARTING = "starting"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERRORED = "errored"


_SESSION_ID_RE = re.compile(SESSION_ID_PATTERN)
_USER_ID_RE = re.compile(USER_ID_PATTERN)


class StreamingSession(BaseDomainModel):
    """One live streaming-transcription session.

    `gpu_instance_id` becomes populated once the spot instance backing
    the session is acquired. `ttl` (seconds-since-epoch) is consumed by
    DynamoDB TTL to scrub abandoned sessions automatically.
    """

    id: str = Field(..., description="Session identifier in the form `sess_<token>`")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    status: SessionStatus
    gpu_instance_id: str | None = Field(
        default=None,
        description="EC2 instance id once the GPU spot is bound; None while starting",
    )
    created_at: datetime
    updated_at: datetime
    ttl: int | None = Field(
        default=None,
        description="DynamoDB TTL (epoch seconds) for automatic cleanup",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Reject session ids that do not match the documented pattern."""
        if not _SESSION_ID_RE.match(value):
            raise ValueError(
                f"id must match pattern {SESSION_ID_PATTERN!r} (got: {value!r})"
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

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_timestamps_utc(cls, value: datetime) -> datetime:
        """Require timezone-aware UTC datetimes on both timestamps."""
        return validate_utc_datetime(value)
