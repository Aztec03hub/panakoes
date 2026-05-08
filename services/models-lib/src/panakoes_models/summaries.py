"""AI-generated summary records.

`Summary` captures the result of a Claude run over a transcript.
Tier (`haiku` vs `sonnet`) reflects the project's cost-discipline split:
Haiku is the default; Sonnet is the paid-tier deep-summary feature.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from panakoes_models._common import BaseDomainModel, validate_utc_datetime
from panakoes_models.transcripts import TRANSCRIPT_ID_PATTERN
from panakoes_models.users import USER_ID_PATTERN

SUMMARY_ID_PATTERN = r"^sum_[a-zA-Z0-9]{16,}$"

SummaryId = Annotated[
    str,
    StringConstraints(pattern=SUMMARY_ID_PATTERN, strip_whitespace=True),
]
"""String newtype for summary identifiers."""


class SummaryTier(StrEnum):
    """Summary tier mirroring the Claude model split.

    `HAIKU` is the cost-disciplined default for free users.
    `SONNET` is reserved for paid tiers' deep-summary feature.
    """

    HAIKU = "haiku"
    SONNET = "sonnet"


_SUMMARY_ID_RE = re.compile(SUMMARY_ID_PATTERN)
_TRANSCRIPT_ID_RE = re.compile(TRANSCRIPT_ID_PATTERN)
_USER_ID_RE = re.compile(USER_ID_PATTERN)


class Summary(BaseDomainModel):
    """A single AI-generated summary of a transcript.

    `prompt_tokens` and `completion_tokens` come straight from the
    Anthropic API's `usage` block; we persist them so billing can
    reconcile against Stripe meters without re-running the model.
    """

    id: str = Field(..., description="Summary identifier in the form `sum_<token>`")
    transcript_id: str = Field(..., description="Source transcript id; matches `TranscriptId`")
    user_id: str = Field(..., description="Owning user; matches `UserId`")
    tier: SummaryTier
    text: str = Field(..., description="Generated summary text")
    model: str = Field(..., min_length=1, description="Anthropic model identifier")
    created_at: datetime
    prompt_tokens: int = Field(..., ge=0, description="Tokens consumed by the prompt")
    completion_tokens: int = Field(..., ge=0, description="Tokens emitted by the model")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        """Reject summary ids that do not match the documented pattern."""
        if not _SUMMARY_ID_RE.match(value):
            raise ValueError(f"id must match pattern {SUMMARY_ID_PATTERN!r} (got: {value!r})")
        return value

    @field_validator("transcript_id")
    @classmethod
    def _validate_transcript_id(cls, value: str) -> str:
        """Reject transcript ids that do not match the documented pattern."""
        if not _TRANSCRIPT_ID_RE.match(value):
            raise ValueError(
                f"transcript_id must match pattern {TRANSCRIPT_ID_PATTERN!r} (got: {value!r})"
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
