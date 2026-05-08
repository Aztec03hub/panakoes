"""Pydantic request/response models and the session-status state machine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The streaming-session lifecycle. `starting` is the entry state right
# after `POST /sessions`; the GPU spawner flips us to `active` when its
# instance is warm; clients can pause and resume; `completed` and
# `errored` are terminal.
SessionStatus = Literal["starting", "active", "paused", "completed", "errored"]

# Valid state transitions. Read as: from-state -> set of allowed
# next-states. Anything not listed is rejected with HTTP 409.
ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    "starting": frozenset({"active", "errored"}),
    "active": frozenset({"paused", "completed", "errored"}),
    "paused": frozenset({"active", "completed", "errored"}),
    "completed": frozenset(),
    "errored": frozenset(),
}


def is_terminal(status: SessionStatus) -> bool:
    """Return True if `status` is a terminal state (no transitions allowed)."""
    return status in {"completed", "errored"}


def is_allowed_transition(from_status: SessionStatus, to_status: SessionStatus) -> bool:
    """Return True if moving from `from_status` to `to_status` is allowed."""
    return to_status in ALLOWED_TRANSITIONS[from_status]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class CreateSessionRequest(BaseModel):
    """Request body for `POST /sessions`.

    `language` is an optional ISO-639-1 hint forwarded to the
    transcription model. Default `en` matches the bulk of expected
    traffic; other values are accepted but not validated against the
    full ISO list (the transcription stage rejects unknown codes).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    language: str = Field(default="en", min_length=2, max_length=8)


class UpdateSessionRequest(BaseModel):
    """Request body for `PATCH /sessions/{session_id}`.

    Both fields are optional but at least one MUST be provided; an empty
    body is rejected. Status transitions are validated against
    `ALLOWED_TRANSITIONS`. `gpu_instance_id` is the EC2 instance id the
    streaming spawner attaches once the GPU is warm.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: SessionStatus | None = None
    gpu_instance_id: str | None = Field(default=None, min_length=1, max_length=64)


class SessionRecord(BaseModel):
    """Full session record as stored in DynamoDB and returned to clients.

    `expires_at` is exposed as epoch seconds (DynamoDB TTL contract);
    `created_at` and `updated_at` use ISO-8601 UTC strings to match
    every other record type in Panakoes.
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    user_id: str
    status: SessionStatus
    language: str
    gpu_instance_id: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: int


class SessionListResponse(BaseModel):
    """Response body for `GET /sessions`."""

    items: list[SessionRecord]
    next_cursor: str | None = None
