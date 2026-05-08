"""Request and response Pydantic models for the summarization API.

The shapes are deliberately strict (`extra="forbid"`, non-empty IDs)
so that bad client input fails at the FastAPI boundary rather than
deep inside a Claude call or an S3 write.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Tier = Literal["haiku", "sonnet"]


class HealthResponse(BaseModel):
    """Response shape for `GET /health`."""

    status: str
    service: str


class SummarizeRequest(BaseModel):
    """Request body for `POST /summarize`.

    `tier` selects the Claude model: `haiku` for the cost-default
    (`claude-haiku-4-5`) and `sonnet` for the paid-tier deep summary
    (`claude-sonnet-4-6`), per the locked decision in CLAUDE.md.
    """

    model_config = ConfigDict(extra="forbid")

    transcript_id: str = Field(..., min_length=1, max_length=128)
    tier: Tier = "haiku"


class SummaryRecord(BaseModel):
    """Public representation of a stored summary."""

    model_config = ConfigDict(extra="forbid")

    transcript_id: str
    user_id: str
    tier: Tier
    model: str
    summary: str
    s3_key: str
    created_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0


class SummariesPage(BaseModel):
    """Paginated list of summaries owned by the authenticated user."""

    model_config = ConfigDict(extra="forbid")

    items: list[SummaryRecord]
    next_cursor: str | None = None
