"""`AuditEvent` Pydantic model and validators.

Each consuming service constructs an `AuditEvent` (often via
`record_event`, which builds it for them). The model enforces every
invariant the audit log relies on: timestamps in UTC, actor/resource
identifiers non-empty, the action string in `domain.verb` form, and
request_id auto-generated when omitted so traces always join up.

Failing fast at construction time is deliberate: a malformed audit
event is a bug, and we want it to surface as a Pydantic
`ValidationError` next to the call site, not as a silent gap in the
audit trail.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ActorType = Literal["user", "service", "system", "anonymous"]

_ACTION_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC `datetime`.

    Wrapped in a module-level helper so tests can monkey-patch the clock
    when checking edge conditions around the past-or-present invariant.
    """
    return datetime.now(UTC)


def _new_request_id() -> str:
    """Generate a fresh UUID4 string used as the default `request_id`.

    Wrapped so the default factory and validator share the same
    implementation; the str(UUID) form is what callers expect to see
    in logs and downstream identifiers.
    """
    return str(uuid.uuid4())


class AuditEvent(BaseModel):
    """A single audit event recorded by a Panakoes service.

    Construction validates every field. Use this model directly when
    you want explicit control over fields, or call `record_event` to
    build and persist in one shot.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
        frozen=False,
    )

    timestamp: datetime = Field(default_factory=_utcnow)
    actor_id: str = Field(..., min_length=1)
    actor_type: ActorType
    action: str
    resource_type: str = Field(..., min_length=1)
    resource_id: str = Field(..., min_length=1)
    source_service: str = Field(..., min_length=1)
    request_id: str = Field(default_factory=_new_request_id, min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    source_ip: str | None = None

    @field_validator("action")
    @classmethod
    def _validate_action_pattern(cls, value: str) -> str:
        """Reject action strings that do not match `domain.verb`.

        Examples: `transcript.created`, `user.signed_in`. The pattern
        is intentionally narrow so audit dashboards can group events
        by domain prefix without parsing surprises.
        """
        if not _ACTION_RE.match(value):
            raise ValueError(
                "action must match pattern '^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$' "
                f"(got: {value!r})"
            )
        return value

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp_utc(cls, value: datetime) -> datetime:
        """Require the timestamp to be timezone-aware and UTC.

        Naive datetimes silently misorder events across time zones, so
        we force the caller (or the default factory) to use UTC.
        """
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        if value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("timestamp must be in UTC")
        return value

    @model_validator(mode="after")
    def _validate_timestamp_not_future(self) -> AuditEvent:
        """Prevent a timestamp set in the future.

        A small grace window absorbs minor clock skew. Anything beyond
        that is almost certainly a programming error; surface it loudly.
        """
        # Allow up to 5 seconds of skew between caller and validator.
        now = _utcnow()
        if (self.timestamp - now).total_seconds() > 5:
            raise ValueError("timestamp must be in the past or present (UTC)")
        return self
