"""Shared pytest fixtures for the Notification API.

Per ADR-018 we do NOT mock the database in integration tests; we use
moto to stand up real-shape SES and DynamoDB in-memory. JWTs are
signed with a deterministic test secret and verified end-to-end. The
audit library is swapped to its in-memory store on every test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3
import pytest
import pytest_asyncio
from jose import jwt
from moto import mock_aws
from panakoes_audit import MemoryAuditStore, reset_store, set_store

from panakoes_notification.clients.ses import SESEmailSender
from panakoes_notification.config import Settings
from panakoes_notification.routes.notify import (
    get_notification_store,
    get_ses_sender,
    get_settings,
    get_template_renderer,
    get_webhook_client,
)
from panakoes_notification.storage.dynamodb import NotificationStore
from panakoes_notification.templates_loader import TemplateRenderer

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"
TEST_TABLE_NAME = "panakoes-notification-test"
TEST_FROM_ADDRESS = "no-reply@panakoes.test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "SES_FROM_ADDRESS",
        "DDB_NOTIFICATION_TABLE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AUDIT_BACKEND",
        "AUDIT_TABLE_NAME",
        "AUDIT_AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("JWT_ISSUER", TEST_JWT_ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", TEST_JWT_AUDIENCE)
    monkeypatch.setenv("SES_FROM_ADDRESS", TEST_FROM_ADDRESS)
    monkeypatch.setenv("DDB_NOTIFICATION_TABLE", TEST_TABLE_NAME)
    monkeypatch.setenv("AWS_REGION", TEST_REGION)
    # moto needs *something* in the AWS creds slots so boto3 client init is happy.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    # Wire NoOp OTel providers; the FastAPI lifespan calls
    # `panakoes_otel.configure()` which honors this env var.
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    yield


@pytest.fixture(autouse=True)
def _audit_memory_store() -> Iterator[MemoryAuditStore]:
    """Swap the audit library to its in-memory store for every test."""
    store = MemoryAuditStore()
    set_store(store)
    yield store
    reset_store()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Clear the lru_cached settings between tests so env changes take effect."""
    from panakoes_notification import auth as auth_module

    auth_module._settings.cache_clear()
    yield
    auth_module._settings.cache_clear()


@pytest.fixture
def test_settings() -> Settings:
    """Return a `Settings` instance pinned to the test env values."""
    return Settings()


def _make_token(
    *,
    sub: str = "user_test_123",
    email: str = "test@panakoes.test",
    jti: str = "session_abc",
    actor_type: str | None = None,
    issuer: str = TEST_JWT_ISSUER,
    audience: str = TEST_JWT_AUDIENCE,
    secret: str = TEST_JWT_SECRET,
    expires_delta: timedelta = timedelta(hours=1),
    issued_at: datetime | None = None,
) -> str:
    """Sign an HS256 JWT with the documented payload shape."""
    issued = issued_at or datetime.now(UTC)
    expires = issued + expires_delta
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "jti": jti,
        "iss": issuer,
        "aud": audience,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if actor_type is not None:
        payload["actor_type"] = actor_type
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def make_token() -> Any:
    """Expose `_make_token` as a fixture so tests can build custom JWTs."""
    return _make_token


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Authorization header signed with the default test claims."""
    return {"Authorization": f"Bearer {_make_token()}"}


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto for SES + DynamoDB + STS for the duration of a test."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mocks: None) -> Any:
    """Provision the notification DynamoDB table inside the moto mock."""
    assert aws_mocks is None  # consume the fixture
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_TABLE_NAME)


@pytest.fixture
def ses_verified_sender(aws_mocks: None) -> str:
    """Verify the SES sender identity inside the moto mock so send_email works."""
    assert aws_mocks is None  # consume the fixture
    client = boto3.client("ses", region_name=TEST_REGION)
    client.verify_email_identity(EmailAddress=TEST_FROM_ADDRESS)
    return TEST_FROM_ADDRESS


@pytest_asyncio.fixture
async def async_client(
    dynamodb_table: Any,
    ses_verified_sender: str,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_notification.main import app

    # Build adapters under the active moto mock so they share state.
    store = NotificationStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    sender = SESEmailSender(from_address=ses_verified_sender, region_name=TEST_REGION)
    renderer = TemplateRenderer()

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_notification_store] = lambda: store
    app.dependency_overrides[get_ses_sender] = lambda: sender
    app.dependency_overrides[get_template_renderer] = lambda: renderer
    # Webhook client built fresh per test by tests that exercise the route;
    # default override returns a shared instance the tests can inject via
    # `dependency_overrides[get_webhook_client]` directly.

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def async_client_with_webhook(
    async_client: AsyncClient,
) -> AsyncIterator[AsyncClient]:
    """Variant of `async_client` that wires the real `WebhookClient` per request.

    Webhook tests use `respx` to mock outbound HTTP, so each request gets
    a fresh `WebhookClient` with a short backoff so retries finish fast.
    """
    from panakoes_notification.clients.webhook import WebhookClient
    from panakoes_notification.main import app

    def _build_client() -> WebhookClient:
        return WebhookClient(
            max_attempts=3,
            base_backoff_seconds=0.0,
            request_timeout_seconds=2.0,
        )

    app.dependency_overrides[get_webhook_client] = _build_client
    yield async_client


# Re-export so individual tests can call it without importing private helpers.
make_jwt = _make_token
