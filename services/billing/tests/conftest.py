"""Shared pytest fixtures for the Billing service.

Per ADR-018 we do NOT mock the database; we use moto to stand up a
real-shape DynamoDB in-memory. Stripe SDK calls are intercepted via a
fake adapter so unit tests never reach Stripe; the SDK adapter itself
is exercised through targeted unit tests with monkey-patched module
attributes (still no network).

JWTs are signed with a deterministic test secret and verified through
the same code path production uses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3
import pytest
import pytest_asyncio
import jwt
from moto import mock_aws
from panakoes_audit import MemoryAuditStore, reset_store, set_store

from panakoes_billing.config import Settings
from panakoes_billing.routes.billing import (
    get_event_store,
    get_settings,
    get_stripe_adapter,
    get_subscription_store,
)
from panakoes_billing.storage.dynamodb import BillingEventStore
from panakoes_billing.storage.subscriptions import SubscriptionStore

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "billing-unit-test-shared-secret!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"
TEST_TABLE_NAME = "panakoes-dev-billing-events-test"
TEST_SUBSCRIPTIONS_TABLE_NAME = "panakoes-dev-subscriptions-test"
TEST_REGION = "us-east-1"

TEST_STRIPE_API_KEY = "sk_test_unit_test_only"
TEST_STRIPE_WEBHOOK_SECRET = "whsec_unit_test_secret"
TEST_PRICE_PRO = "price_test_pro_unit"
TEST_PRICE_TEAM = "price_test_team_unit"
TEST_SUCCESS_URL = "https://app.panakoes.test/billing/success"
TEST_CANCEL_URL = "https://app.panakoes.test/billing/cancel"
TEST_PORTAL_RETURN_URL = "https://app.panakoes.test/account"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "STRIPE_API_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "STRIPE_PRICE_PRO",
        "STRIPE_PRICE_TEAM",
        "STRIPE_SUCCESS_URL",
        "STRIPE_CANCEL_URL",
        "STRIPE_PORTAL_RETURN_URL",
        "DDB_BILLING_TABLE",
        "DDB_SUBSCRIPTIONS_TABLE",
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
    monkeypatch.setenv("STRIPE_API_KEY", TEST_STRIPE_API_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_STRIPE_WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_PRO", TEST_PRICE_PRO)
    monkeypatch.setenv("STRIPE_PRICE_TEAM", TEST_PRICE_TEAM)
    monkeypatch.setenv("STRIPE_SUCCESS_URL", TEST_SUCCESS_URL)
    monkeypatch.setenv("STRIPE_CANCEL_URL", TEST_CANCEL_URL)
    monkeypatch.setenv("STRIPE_PORTAL_RETURN_URL", TEST_PORTAL_RETURN_URL)
    monkeypatch.setenv("DDB_BILLING_TABLE", TEST_TABLE_NAME)
    monkeypatch.setenv("DDB_SUBSCRIPTIONS_TABLE", TEST_SUBSCRIPTIONS_TABLE_NAME)
    # moto needs *something* in the AWS creds slots so boto3 client init is happy.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    yield


@pytest.fixture(autouse=True)
def _audit_memory_store() -> Iterator[MemoryAuditStore]:
    """Swap the audit library to its in-memory store for every test."""
    store = MemoryAuditStore()
    set_store(store)
    yield store
    reset_store()


@pytest.fixture
def test_settings() -> Settings:
    """Return a `Settings` instance pinned to the test env values."""
    return Settings()


def _make_token(
    *,
    sub: str = "user_billing_test_123",
    email: str = "billing-test@panakoes.test",
    jti: str = "session_billing_abc",
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
    """Activate moto for DynamoDB + STS for the duration of a test."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mocks: None) -> Any:
    """Provision the billing-events DynamoDB table inside the moto mock."""
    assert aws_mocks is None  # consume the fixture; moto must be active
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
def subscriptions_table(aws_mocks: None) -> Any:
    """Provision the subscriptions DynamoDB table inside the moto mock."""
    assert aws_mocks is None
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_SUBSCRIPTIONS_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "tenant_id", "KeyType": "HASH"},
            {"AttributeName": "subscription_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "subscription_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_SUBSCRIPTIONS_TABLE_NAME)


class FakeStripeAdapter:
    """Hand-rolled fake `StripeAdapter` for integration tests.

    Records every call so tests can assert on inputs, returns canned
    responses, and lets tests flip a flag to make signature verification
    fail on demand.
    """

    def __init__(self) -> None:
        """Initialize empty call logs and default canned responses."""
        self.checkout_calls: list[dict[str, Any]] = []
        self.portal_calls: list[dict[str, Any]] = []
        self.webhook_calls: list[dict[str, Any]] = []
        self.customer_calls: list[dict[str, Any]] = []

        self.checkout_response: dict[str, Any] = {
            "id": "cs_test_unit_session",
            "url": "https://checkout.stripe.test/session/cs_test_unit_session",
        }
        self.portal_response: dict[str, Any] = {
            "id": "bps_test_portal",
            "url": "https://billing.stripe.test/portal/bps_test_portal",
        }
        self.customer_response: dict[str, Any] = {
            "id": "cus_test_created_by_fake",
            "email": "fake@panakoes.test",
        }
        # When non-empty, signature verification raises StripeSignatureError
        # with this message regardless of the actual payload/secret. Lets
        # us simulate the negative path without a real Stripe SDK call.
        self.fail_signature_with: str | None = None
        # When set, return this event dict on a successful verification
        # instead of the default echo of the parsed payload.
        self.next_event_override: dict[str, Any] | None = None

    def create_checkout_session(self, **kwargs: Any) -> dict[str, Any]:
        """Record the call and return the canned checkout response."""
        self.checkout_calls.append(kwargs)
        return self.checkout_response

    def create_portal_session(self, **kwargs: Any) -> dict[str, Any]:
        """Record the call and return the canned portal response."""
        self.portal_calls.append(kwargs)
        return self.portal_response

    def create_customer(self, **kwargs: Any) -> dict[str, Any]:
        """Record the call and return the canned customer response."""
        self.customer_calls.append(kwargs)
        return self.customer_response

    def verify_webhook_signature(self, **kwargs: Any) -> dict[str, Any]:
        """Record the call. Either raise or return a parsed event."""
        from panakoes_billing.stripe_client import StripeSignatureError

        self.webhook_calls.append(kwargs)
        if self.fail_signature_with is not None:
            raise StripeSignatureError(self.fail_signature_with)
        if self.next_event_override is not None:
            return self.next_event_override
        # Default: parse the raw JSON payload as the event so simple
        # tests can pass a dict via `json.dumps(...)` and read it back.
        import json

        payload = kwargs["payload"]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        parsed: Any = json.loads(payload) if payload else {}
        if not isinstance(parsed, dict):
            return {}
        return parsed


@pytest.fixture
def fake_stripe() -> FakeStripeAdapter:
    """Return a `FakeStripeAdapter` for integration tests to assert against."""
    return FakeStripeAdapter()


@pytest_asyncio.fixture
async def async_client(
    dynamodb_table: Any,
    subscriptions_table: Any,
    fake_stripe: FakeStripeAdapter,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_billing.main import app

    assert subscriptions_table is not None
    # Build the boto3-backed stores under the active moto mock so they
    # share state with the table fixtures.
    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    sub_store = SubscriptionStore(
        table_name=TEST_SUBSCRIPTIONS_TABLE_NAME,
        region_name=TEST_REGION,
    )

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_event_store] = lambda: store
    app.dependency_overrides[get_subscription_store] = lambda: sub_store
    app.dependency_overrides[get_stripe_adapter] = lambda: fake_stripe

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# Re-export so individual tests can call it without importing private helpers.
make_jwt = _make_token
