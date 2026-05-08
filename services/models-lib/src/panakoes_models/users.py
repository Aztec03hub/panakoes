"""User identity and account models.

A `User` is the principal that owns ingestions, transcripts, summaries,
and subscriptions. Identifiers are validated at construction so a
malformed `UserId` cannot leak past the contract boundary.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import EmailStr, Field, StringConstraints, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime

USER_ID_PATTERN = r"^user_[a-zA-Z0-9]{12,}$"

UserId = Annotated[
    str,
    StringConstraints(pattern=USER_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for user identifiers; enforced via Pydantic regex."""

UserRole = Literal["user", "admin"]
UserTier = Literal["free", "pro", "team"]

_USER_ID_RE = re.compile(USER_ID_PATTERN)


class User(BaseDomainModel):
    """A registered Panakoes user.

    `email` uses `pydantic.EmailStr` for RFC-compliant validation; the
    `id` field is checked against the `UserId` regex on construction.
    """

    id: str = Field(..., description="User identifier in the form `user_<token>`")
    email: EmailStr
    created_at: datetime
    role: UserRole
    tier: UserTier

    @field_validator("id")
    @classmethod
    def _validate_user_id(cls, value: str) -> str:
        """Reject identifiers that do not match the `UserId` pattern."""
        if not _USER_ID_RE.match(value):
            raise ValueError(f"id must match pattern {USER_ID_PATTERN!r} (got: {value!r})")
        return value

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_utc(cls, value: datetime) -> datetime:
        """Require `created_at` to be a timezone-aware UTC datetime."""
        return validate_utc_datetime(value)
