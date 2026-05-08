"""Cross-service API error envelope.

Every Panakoes HTTP service returns errors in this exact shape so the
SvelteKit frontend (and any future SDK) can render them uniformly.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from panakoes_models._common import BaseDomainModel


class ApiError(BaseDomainModel):
    """Canonical API error envelope.

    `code` is a stable machine-readable identifier (e.g. `auth.invalid_token`).
    `message` is a human-readable string safe to surface in UIs.
    `request_id` joins the error to the request log for support workflows.
    `details` is a free-form bag for field-level validation errors etc.
    """

    code: str = Field(..., min_length=1, description="Stable machine-readable error code")
    message: str = Field(..., min_length=1, description="Human-readable error message")
    request_id: str | None = Field(
        default=None, description="Request id correlating this error to logs"
    )
    details: dict[str, Any] | None = Field(
        default=None, description="Optional structured context (e.g. field validation errors)"
    )
