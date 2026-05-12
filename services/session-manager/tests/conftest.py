"""Shared pytest fixtures for the Session Manager.

Per ADR-018 we do NOT mock the database in integration tests; we use
moto to stand up a real-shape DynamoDB in-memory. JWTs are signed with
a deterministic test secret and verified end-to-end.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3
import pytest
import pytest_asyncio
from jose import jwt
from moto import mock_aws
from panakoes_audit import MemoryAuditStore, reset_store, set_store

from panakoes_session_manager.config import Settings
from panakoes_session_manager.routes.sessions import get_session_store, get_settings
from panakoes_session_manager.storage.dynamodb import SessionStore

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"
TEST_TABLE_NAME = "panakoes-streaming-sessions-test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "SESSIONS_TABLE_NAME",
        "SESSION_TTL_SECONDS",
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
    monkeypatch.setenv("SESSIONS_TABLE_NAME", TEST_TABLE_NAME)
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
    sub: str = "user_test_123",
    email: str = "test@panakoes.test",
    jti: str = "session_abc",
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
    """Provision the streaming-sessions DynamoDB table inside the moto mock.

    Mirrors the Terraform definition in `infra/dev/data/main.tf`:
    hash key `session_id`, GSI `UserSessionsIndex` on `user_id` +
    `created_at`, GSI `ActiveSessionsIndex` on `status` + `created_at`.
    """
    assert aws_mocks is None  # mypy / linter: consume the fixture
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_TABLE_NAME,
        KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "created_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "UserSessionsIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            },
            {
                "IndexName": "ActiveSessionsIndex",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "created_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_TABLE_NAME)


@pytest_asyncio.fixture
async def async_client(
    dynamodb_table: Any,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_session_manager.main import app

    # Build the boto3-backed adapter under the active moto mock so it
    # shares state with the dynamodb_table fixture.
    store = SessionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_session_store] = lambda: store

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# Re-export so individual tests can call it without importing private helpers.
make_jwt = _make_token


@pytest.fixture
def ensure_aws_creds() -> Iterator[None]:
    """Helper for tests that initialize boto clients outside `async_client`.

    Ensures the moto-friendly AWS env vars are present even when a test
    only requests this fixture (without dynamodb_table).
    """
    if "AWS_ACCESS_KEY_ID" not in os.environ:
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    if "AWS_SECRET_ACCESS_KEY" not in os.environ:
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    if "AWS_DEFAULT_REGION" not in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = TEST_REGION
    yield
