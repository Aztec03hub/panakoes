"""Pydantic request and response models for the Notification API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

NotificationChannel = Literal["email", "webhook"]
NotificationStatus = Literal["sent", "failed"]


class HealthResponse(BaseModel):
    """Schema returned by `/health`."""

    status: str
    service: str


class SendEmailRequest(BaseModel):
    """Request body for `POST /notify/email`.

    `template` is the basename of a Jinja2 template under
    `services/notification/templates/`. The service loads
    `<template>.txt.j2` for the plaintext body and, when present, also
    `<template>.html.j2` for an HTML alternative. `vars` is the render
    context.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    to: EmailStr
    subject: str = Field(..., min_length=1, max_length=512)
    template: str = Field(..., min_length=1, max_length=128)
    vars: dict[str, Any] = Field(default_factory=dict)


class SendWebhookRequest(BaseModel):
    """Request body for `POST /notify/webhook`.

    The endpoint POSTs `payload` as JSON to `url` with optional extra
    headers. URL must be HTTPS; `http://` is rejected at validation
    time so private network exfiltration paths stay closed.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: HttpUrl
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] | None = None


class NotificationRecord(BaseModel):
    """Full notification record as stored in DynamoDB."""

    model_config = ConfigDict(extra="ignore")

    notification_id: str
    user_id: str
    channel: NotificationChannel
    status: NotificationStatus
    target: str
    subject: str | None = None
    template: str | None = None
    error: str | None = None
    attempts: int = 1
    created_at: datetime


class SendNotificationResponse(BaseModel):
    """Response body for the two `POST /notify/*` endpoints."""

    notification_id: str
    status: NotificationStatus
    attempts: int = 1


class NotificationListResponse(BaseModel):
    """Response body for `GET /notifications`."""

    items: list[NotificationRecord]
    next_cursor: str | None = None
