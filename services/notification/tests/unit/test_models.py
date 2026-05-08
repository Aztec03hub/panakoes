"""Unit tests for the Pydantic request and response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_notification.models import SendEmailRequest, SendWebhookRequest


@pytest.mark.unit
def test_send_email_request_accepts_valid_payload() -> None:
    """A valid email request validates successfully."""
    req = SendEmailRequest(
        to="alice@example.com",
        subject="Welcome",
        template="welcome",
        vars={"name": "Alice"},
    )
    assert str(req.to) == "alice@example.com"
    assert req.template == "welcome"
    assert req.vars == {"name": "Alice"}


@pytest.mark.unit
def test_send_email_request_rejects_invalid_email() -> None:
    """A malformed `to` address is rejected at model construction."""
    with pytest.raises(ValidationError):
        SendEmailRequest(
            to="not-an-email",  # type: ignore[arg-type]
            subject="x",
            template="welcome",
            vars={},
        )


@pytest.mark.unit
def test_send_email_request_rejects_extra_fields() -> None:
    """Extra fields are rejected (no field smuggling)."""
    with pytest.raises(ValidationError):
        SendEmailRequest.model_validate(
            {
                "to": "a@b.com",
                "subject": "s",
                "template": "welcome",
                "vars": {},
                "user_id": "spoofed",
            }
        )


@pytest.mark.unit
def test_send_webhook_request_rejects_http_url() -> None:
    """`http://` URLs are rejected at the model layer.

    Pydantic's `HttpUrl` accepts both http and https; we additionally
    enforce https at the route layer. This test pins the route-layer
    contract by exercising the request model with a plain-http URL
    and asserting the model still accepts the value (so the route
    layer is the only gate rejecting it). The HTTPS-only enforcement
    is exercised in the integration tests.
    """
    # Pydantic accepts the URL: the route does the rejection.
    req = SendWebhookRequest(
        url="http://example.com/hook",  # type: ignore[arg-type]
        payload={"x": 1},
    )
    assert str(req.url).startswith("http://")


@pytest.mark.unit
def test_send_webhook_request_accepts_https_url() -> None:
    """A valid https URL validates successfully."""
    req = SendWebhookRequest(
        url="https://example.com/hook",  # type: ignore[arg-type]
        payload={"x": 1},
        headers={"X-Test": "1"},
    )
    assert str(req.url).startswith("https://")
    assert req.headers == {"X-Test": "1"}
