"""Pydantic request and response models for the GPU Spawner."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Subset of the EC2 instance state machine we surface to callers.
# AWS uses these exact strings; we restate them as a Literal so the
# response schema is documented in the OpenAPI output.
InstanceState = Literal[
    "pending",
    "running",
    "shutting-down",
    "terminated",
    "stopping",
    "stopped",
    "unknown",
]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class SpawnRequest(BaseModel):
    """Request body for `POST /spawn`.

    `session_id` and `user_id` are propagated to the launched instance
    as tags so subsequent lifecycle calls can be authorized via the
    same tag-based ownership model the IAM policy uses.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(..., min_length=1, max_length=128)
    user_id: str = Field(..., min_length=1, max_length=128)


class SpawnResponse(BaseModel):
    """Response body for `POST /spawn`."""

    instance_id: str


class InstanceStateResponse(BaseModel):
    """Response body for `GET /spawn/{instance_id}`."""

    instance_id: str
    state: InstanceState
    session_id: str | None = None
    user_id: str | None = None


class TerminateResponse(BaseModel):
    """Response body for `DELETE /spawn/{instance_id}`."""

    instance_id: str
    previous_state: InstanceState
    current_state: InstanceState
