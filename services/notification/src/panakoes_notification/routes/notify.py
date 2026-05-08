"""Send-side endpoints: `POST /notify/email` and `POST /notify/webhook`.

Both endpoints persist a `NotificationRecord` (sent or failed) and
emit an audit event. The webhook endpoint additionally enforces
HTTPS-only at the request layer, ahead of any retry logic, so we
never expand redirects or backoff against `http://` URLs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from panakoes_audit import record_event
from ulid import ULID

from panakoes_notification.auth import AuthenticatedUser, get_current_user
from panakoes_notification.clients.ses import SESEmailSender
from panakoes_notification.clients.webhook import WebhookClient
from panakoes_notification.config import Settings
from panakoes_notification.models import (
    NotificationRecord,
    SendEmailRequest,
    SendNotificationResponse,
    SendWebhookRequest,
)
from panakoes_notification.storage.dynamodb import NotificationStore
from panakoes_notification.templates_loader import (
    TemplateNotFoundError,
    TemplateRenderer,
)

router = APIRouter(prefix="/notify", tags=["notify"])


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request."""
    return Settings()


def get_notification_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> NotificationStore:
    """FastAPI dependency: provide a DynamoDB-backed `NotificationStore`."""
    return NotificationStore(
        table_name=settings.ddb_notification_table,
        region_name=settings.aws_region,
    )


def get_template_renderer() -> TemplateRenderer:
    """FastAPI dependency: provide the Jinja2 template renderer."""
    return TemplateRenderer()


def get_ses_sender(
    settings: Annotated[Settings, Depends(get_settings)],
) -> SESEmailSender:
    """FastAPI dependency: provide an SES email sender."""
    return SESEmailSender(
        from_address=settings.ses_from_address,
        region_name=settings.aws_region,
    )


def get_webhook_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebhookClient:
    """FastAPI dependency: provide a `WebhookClient`.

    Each request gets a fresh client so tests can inject `respx`
    without contending over a shared httpx instance. The route uses
    `await client.aclose()` once it is done.
    """
    return WebhookClient(
        max_attempts=settings.webhook_max_attempts,
        base_backoff_seconds=settings.webhook_base_backoff_seconds,
        request_timeout_seconds=settings.webhook_request_timeout_seconds,
    )


@router.post(
    "/email",
    response_model=SendNotificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_email(
    request: SendEmailRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[NotificationStore, Depends(get_notification_store)],
    renderer: Annotated[TemplateRenderer, Depends(get_template_renderer)],
    sender: Annotated[SESEmailSender, Depends(get_ses_sender)],
) -> SendNotificationResponse:
    """Render the named template and send it via SES."""
    try:
        rendered = renderer.render(request.template, request.vars)
    except TemplateNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"template not found: {request.template}",
        ) from exc

    notification_id = str(ULID())
    now = datetime.now(UTC)

    sender.send(
        to_address=str(request.to),
        subject=request.subject,
        text_body=rendered.text,
        html_body=rendered.html,
    )

    record = NotificationRecord(
        notification_id=notification_id,
        user_id=user.user_id,
        channel="email",
        status="sent",
        target=str(request.to),
        subject=request.subject,
        template=request.template,
        attempts=1,
        created_at=now,
    )
    store.put(record)

    await record_event(
        actor_id=user.user_id,
        actor_type=user.actor_type,
        action="notification.email_sent",
        resource_type="notification",
        resource_id=notification_id,
        source_service="notification",
        details={
            "template": request.template,
            "to": str(request.to),
            "subject": request.subject,
        },
    )

    return SendNotificationResponse(
        notification_id=notification_id,
        status="sent",
        attempts=1,
    )


@router.post(
    "/webhook",
    response_model=SendNotificationResponse,
)
async def send_webhook(
    request: SendWebhookRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[NotificationStore, Depends(get_notification_store)],
    client: Annotated[WebhookClient, Depends(get_webhook_client)],
) -> SendNotificationResponse:
    """POST the JSON payload to `url` and record the outcome."""
    url_str = str(request.url)
    if not url_str.lower().startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook URL must use https://",
        )

    try:
        result = await client.post(
            url=url_str,
            payload=request.payload,
            headers=request.headers,
        )
    finally:
        await client.aclose()

    notification_id = str(ULID())
    now = datetime.now(UTC)

    record = NotificationRecord(
        notification_id=notification_id,
        user_id=user.user_id,
        channel="webhook",
        status="sent" if result.success else "failed",
        target=url_str,
        attempts=result.attempts,
        error=result.error,
        created_at=now,
    )
    store.put(record)

    audit_action = (
        "notification.webhook_sent" if result.success else "notification.webhook_failed"
    )
    await record_event(
        actor_id=user.user_id,
        actor_type=user.actor_type,
        action=audit_action,
        resource_type="notification",
        resource_id=notification_id,
        source_service="notification",
        details={
            "url": url_str,
            "attempts": result.attempts,
            "status_code": result.status_code,
        },
    )

    if not result.success:
        return SendNotificationResponse(
            notification_id=notification_id,
            status="failed",
            attempts=result.attempts,
        )
    return SendNotificationResponse(
        notification_id=notification_id,
        status="sent",
        attempts=result.attempts,
    )
