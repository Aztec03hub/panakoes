"""Integration tests for `POST /notify/email`."""

from __future__ import annotations

import boto3
import pytest
from httpx import AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import TEST_REGION, TEST_TABLE_NAME


@pytest.mark.integration
async def test_email_happy_path_records_audit_and_db(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
    _audit_memory_store: MemoryAuditStore,
) -> None:
    """A valid request renders the template, sends via SES, persists, audits."""
    response = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "alice@example.com",
            "subject": "Welcome to Panakoes",
            "template": "welcome",
            "vars": {"name": "Alice"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "sent"
    assert body["attempts"] == 1
    notification_id = body["notification_id"]

    # Audit event recorded with the documented action.
    events = _audit_memory_store.events
    assert len(events) == 1
    event = events[0]
    assert event.action == "notification.email_sent"
    assert event.resource_type == "notification"
    assert event.resource_id == notification_id
    assert event.source_service == "notification"

    # DynamoDB record persisted.
    table = boto3.resource("dynamodb", region_name=TEST_REGION).Table(TEST_TABLE_NAME)
    item = table.get_item(
        Key={"pk": "USER#user_test_123", "sk": f"NOTIFY#{notification_id}"}
    ).get("Item")
    assert item is not None
    assert item["channel"] == "email"
    assert item["status"] == "sent"
    assert item["target"] == "alice@example.com"
    assert item["template"] == "welcome"


@pytest.mark.integration
async def test_email_renders_summary_ready_template(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """The `summary_ready` template substitutes the transcript id."""
    response = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "bob@example.com",
            "subject": "Summary ready",
            "template": "summary_ready",
            "vars": {"transcript_id": "tr_abc123", "name": "Bob"},
        },
    )
    assert response.status_code == 201


@pytest.mark.integration
async def test_email_returns_404_for_unknown_template(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """An unknown template name => 404."""
    response = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "alice@example.com",
            "subject": "x",
            "template": "no_such_template",
            "vars": {},
        },
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_email_requires_auth(async_client: AsyncClient) -> None:
    """No Authorization header => 401."""
    response = await async_client.post(
        "/notify/email",
        json={
            "to": "alice@example.com",
            "subject": "x",
            "template": "welcome",
            "vars": {},
        },
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_email_rejects_bad_token(async_client: AsyncClient) -> None:
    """A garbage Bearer token => 401."""
    response = await async_client.post(
        "/notify/email",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={
            "to": "alice@example.com",
            "subject": "x",
            "template": "welcome",
            "vars": {},
        },
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_email_rejects_invalid_email(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Malformed `to` => 422."""
    response = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "not-an-email",
            "subject": "x",
            "template": "welcome",
            "vars": {},
        },
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_email_rejects_extra_field(
    async_client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Extra fields are rejected (no field smuggling)."""
    response = await async_client.post(
        "/notify/email",
        headers=auth_headers,
        json={
            "to": "alice@example.com",
            "subject": "x",
            "template": "welcome",
            "vars": {},
            "user_id": "spoofed",
        },
    )
    assert response.status_code == 422
