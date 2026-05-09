"""Domain models for the admin-api Tier 3 lifecycle safety pattern.

The shapes here mirror the TypeScript contracts in
`services/admin/src/lib/types.ts`. Every Tier 3 dangerous operation
moves through the same envelope:

    LifecycleRequest[P]   ->  LifecycleResponse[R]

with `P` the operation-specific params and `R` the operation-specific
result. The envelope's discipline (typed confirmation string +
idempotency key + step-up MFA + audit-before-AND-after) is what makes
the operations safe; the operation handler itself is just the body.

The envelope uses generics in the underlying interfaces and routes; this
module defines the concrete `dict`-payload variants that admin-api stores
and emits over the wire. Each operation route declares its own typed
`P` / `R` Pydantic models and constructs `LifecycleRequest[P]` /
`LifecycleResponse[R]` at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LifecycleStatus(StrEnum):
    """Terminal status of a lifecycle operation."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LifecycleRequest(BaseModel):
    """Request envelope every Tier 3 operation accepts.

    The route layer is responsible for re-validating `params` against
    the operation-specific Pydantic model before invoking the handler.
    Storing `params` as `dict[str, Any]` here keeps this module free of
    operation-specific knowledge so it can stay in `panakoes_admin_api`
    rather than splitting across operation modules.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=256,
        description="UUID minted by the caller. Repeated submissions collapse to one effect.",
    )
    confirmation: str = Field(
        min_length=1,
        max_length=256,
        description="Typed confirmation string the user must type into the UI before submit.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific payload, validated by the route against a typed model.",
    )


class LifecycleResponse(BaseModel):
    """Response envelope every Tier 3 operation returns.

    `result` is set when `status == SUCCEEDED`. `error_message` is set
    when `status == FAILED`. `audit_request_id` joins this row to its
    audit-log entries (one `intent` row written before the handler
    runs, one `outcome` row written after).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str
    status: LifecycleStatus
    result: dict[str, Any] | None = None
    error_message: str | None = None
    audit_request_id: str
    started_at: datetime
    finished_at: datetime | None = None
