"""Shared pytest fixtures for the Ingestion API.

Per ADR-018 we do NOT mock the database in integration tests; we use
moto to stand up a real-shape DynamoDB and S3 in-memory. JWTs are
signed with a deterministic test secret and verified end-to-end.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3
import jwt
import pytest
import pytest_asyncio
from moto import mock_aws
from panakoes_audit import MemoryAuditStore, reset_store, set_store

from panakoes_ingestion_api.config import Settings
from panakoes_ingestion_api.routes.ingestion import (
    get_ingestion_store,
    get_settings,
    get_url_generator,
)
from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.storage.s3 import S3PresignedUrlGenerator

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"
TEST_TABLE_NAME = "panakoes-ingestion-test"
TEST_BUCKET_NAME = "panakoes-audio-uploads-test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "INGESTION_TABLE_NAME",
        "INGESTION_BUCKET",
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
    monkeypatch.setenv("INGESTION_TABLE_NAME", TEST_TABLE_NAME)
    monkeypatch.setenv("INGESTION_BUCKET", TEST_BUCKET_NAME)
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
    """Activate moto for S3 + DynamoDB + STS for the duration of a test."""
    with mock_aws():
        yield


@pytest.fixture
def dynamodb_table(aws_mocks: None) -> Any:
    """Provision the ingestion DynamoDB table inside the moto mock."""
    assert aws_mocks is None  # mypy / linter: consume the fixture
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
def s3_bucket(aws_mocks: None) -> str:
    """Create the upload bucket inside the moto mock."""
    assert aws_mocks is None  # consume the fixture; moto must be active
    client = boto3.client("s3", region_name=TEST_REGION)
    client.create_bucket(Bucket=TEST_BUCKET_NAME)
    return TEST_BUCKET_NAME


@pytest_asyncio.fixture
async def async_client(
    dynamodb_table: Any,
    s3_bucket: str,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_ingestion_api.main import app

    # Build the boto3-backed adapters under the active moto mock so they
    # share state with the dynamodb_table / s3_bucket fixtures.
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    url_generator = S3PresignedUrlGenerator(bucket=TEST_BUCKET_NAME, region_name=TEST_REGION)

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_ingestion_store] = lambda: store
    app.dependency_overrides[get_url_generator] = lambda: url_generator

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
    only requests this fixture (without dynamodb_table / s3_bucket).
    """
    if "AWS_ACCESS_KEY_ID" not in os.environ:
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    if "AWS_SECRET_ACCESS_KEY" not in os.environ:
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    if "AWS_DEFAULT_REGION" not in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = TEST_REGION
    yield
