"""Notification records (email, webhook, sms).

A `NotificationRecord` is what the notification service writes to its
DynamoDB log. The id is a Crockford-base32 ULID-like string so the
identifier itself is sortable by creation time, simplifying queries.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.users import USER_ID_PATTERN

# Crockford-base32 alphabet (ULID-style): no I, L, O, U.
NOTIFICATION_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"

NotificationId = Annotated[
    str,
    StringConstraints(pattern=NOTIFICATION_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for notification identifiers (ULID-shaped)."""


class NotificationKind(StrEnum):
    """Channel a notification is delivered through."""

    EMAIL = "email"
    WEBHOOK = "webhook"
    SMS = "sms"


class NotificationStatus(StrEnum):
    """Delivery state of a single notification."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


_NOTIFICATION_ID_RE = re.compile(NOTIFICATION_ID_PATTERN)
_USER_ID_RE = re.compile(USER_ID_PATTERN)


class NotificationRecord(BaseDomainModel):
    """One notification dispatch attempt.

    `target` carries the channel-specific destination (email address,
    webhook URL, phone number). `template` is the logical template id
    (not the rendered body); the rendered body is not persisted in this
    record because it may contain user content we should not retain.
    """

    id: str = Field(..., description="ULID-like 26-char Crockford-base32 identifier")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    kind: NotificationKind
    status: NotificationStatus
    target: str = Field(..., min_length=1, description="Channel-specific delivery target")
    template: str = Field(..., min_length=1, description="Logical template identifier")
    error: str | None = Field(default=None, description="Populated when status is `failed`")
    sent_at: datetime | None = Field(default=None, description="When delivery succeeded")
    created_at: datetime

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Reject notification ids that do not match the ULID-shape pattern."""
        if not _NOTIFICATION_ID_RE.match(value):
            raise ValueError(
                f"id must match pattern {NOTIFICATION_ID_PATTERN!r} (got: {value!r})"
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

    @field_validator("created_at", "sent_at")
    @classmethod
    def _validate_timestamps_utc(cls, value: datetime | None) -> datetime | None:
        """Require any provided datetime to be timezone-aware UTC."""
        if value is None:
            return None
        return validate_utc_datetime(value)
