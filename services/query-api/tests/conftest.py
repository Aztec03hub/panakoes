"""Shared pytest fixtures for the Query API.

Per ADR-018 we do NOT mock DynamoDB; we stand it up via moto so the
storage adapters exercise their real query paths. JWTs are signed
with a deterministic test secret and verified end-to-end.
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

from panakoes_query_api.config import Settings
from panakoes_query_api.routes.dependencies import (
    get_ingestion_reader,
    get_session_reader,
    get_settings,
    get_summary_reader,
)
from panakoes_query_api.storage.ingestion import IngestionReader
from panakoes_query_api.storage.sessions import SessionReader
from panakoes_query_api.storage.summaries import SummaryReader

if TYPE_CHECKING:
    from httpx import AsyncClient

# Deterministic 32+ byte secret for HS256 in tests.
TEST_JWT_SECRET = "unit-test-shared-secret-32bytes-min!!"
TEST_JWT_ISSUER = "https://auth.panakoes.test"
TEST_JWT_AUDIENCE = "panakoes-api-test"
TEST_INGESTION_TABLE = "panakoes-query-ingestion-test"
TEST_SUMMARIES_TABLE = "panakoes-query-summaries-test"
TEST_SESSIONS_TABLE = "panakoes-query-sessions-test"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    for key in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "DDB_INGESTION_TABLE",
        "DDB_SUMMARIES_TABLE",
        "DDB_SESSIONS_TABLE",
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
    monkeypatch.setenv("DDB_INGESTION_TABLE", TEST_INGESTION_TABLE)
    monkeypatch.setenv("DDB_SUMMARIES_TABLE", TEST_SUMMARIES_TABLE)
    monkeypatch.setenv("DDB_SESSIONS_TABLE", TEST_SESSIONS_TABLE)
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
    """Activate moto for DynamoDB + STS for the duration of a test."""
    with mock_aws():
        yield


@pytest.fixture
def ingestion_table(aws_mocks: None) -> Any:
    """Provision the ingestion DynamoDB table inside the moto mock."""
    assert aws_mocks is None
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_INGESTION_TABLE,
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
    return resource.Table(TEST_INGESTION_TABLE)


@pytest.fixture
def summaries_table(aws_mocks: None) -> Any:
    """Provision the summaries DynamoDB table inside the moto mock."""
    assert aws_mocks is None
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_SUMMARIES_TABLE,
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
    return resource.Table(TEST_SUMMARIES_TABLE)


@pytest.fixture
def sessions_table(aws_mocks: None) -> Any:
    """Provision the streaming-sessions table inside the moto mock.

    Mirrors the production schema in `infra/dev/data/main.tf`:
    base table keyed on `session_id`; GSI `UserSessionsIndex` on
    `user_id` (hash) + `created_at` (range).
    """
    assert aws_mocks is None
    client = boto3.client("dynamodb", region_name=TEST_REGION)
    client.create_table(
        TableName=TEST_SESSIONS_TABLE,
        KeySchema=[
            {"AttributeName": "session_id", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "session_id", "AttributeType": "S"},
            {"AttributeName": "user_id", "AttributeType": "S"},
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
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    resource = boto3.resource("dynamodb", region_name=TEST_REGION)
    return resource.Table(TEST_SESSIONS_TABLE)


def _put_ingestion(
    table: Any,
    *,
    user_id: str,
    ingestion_id: str,
    filename: str = "f.m4a",
    content_type: str = "audio/mp4",
    size_bytes: int = 1,
    s3_key: str | None = None,
    status: str = "pending",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Seed a single ingestion record into the moto-backed table."""
    when = created_at or datetime.now(UTC)
    item = {
        "pk": f"USER#{user_id}",
        "sk": f"INGESTION#{ingestion_id}",
        "ingestion_id": ingestion_id,
        "user_id": user_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "s3_key": s3_key or f"audio/{user_id}/{ingestion_id}/{filename}",
        "status": status,
        "created_at": when.isoformat(),
        "updated_at": when.isoformat(),
    }
    table.put_item(Item=item)
    return item


def _put_summary(
    table: Any,
    *,
    user_id: str,
    transcript_id: str,
    summary_text: str = "Test summary.",
    tier: str = "fast",
    model: str = "claude-haiku-4-5",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Seed a single summary record into the moto-backed table."""
    when = created_at or datetime.now(UTC)
    item = {
        "pk": f"USER#{user_id}",
        "sk": f"SUMMARY#{transcript_id}",
        "transcript_id": transcript_id,
        "user_id": user_id,
        "summary_text": summary_text,
        "tier": tier,
        "model": model,
        "created_at": when.isoformat(),
        "updated_at": when.isoformat(),
    }
    table.put_item(Item=item)
    return item


def _put_session(
    table: Any,
    *,
    user_id: str,
    session_id: str,
    status: str = "active",
    created_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> dict[str, Any]:
    """Seed a single streaming-session record into the moto-backed table."""
    when = created_at or datetime.now(UTC)
    item: dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "status": status,
        "created_at": when.isoformat(),
        "updated_at": when.isoformat(),
    }
    if ended_at is not None:
        item["ended_at"] = ended_at.isoformat()
    table.put_item(Item=item)
    return item


@pytest.fixture
def seed_ingestion() -> Any:
    """Expose the seeder so individual tests can populate the table."""
    return _put_ingestion


@pytest.fixture
def seed_summary() -> Any:
    """Expose the seeder so individual tests can populate the table."""
    return _put_summary


@pytest.fixture
def seed_session() -> Any:
    """Expose the seeder so individual tests can populate the table."""
    return _put_session


@pytest_asyncio.fixture
async def async_client(
    ingestion_table: Any,
    summaries_table: Any,
    sessions_table: Any,
    test_settings: Settings,
) -> AsyncIterator[AsyncClient]:
    """Async httpx client with all dependencies overridden for tests."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_query_api.main import app

    # Build boto3-backed adapters under the active moto mock so they
    # share state with the table fixtures.
    ingestion_reader = IngestionReader(
        table_name=TEST_INGESTION_TABLE, region_name=TEST_REGION
    )
    summary_reader = SummaryReader(
        table_name=TEST_SUMMARIES_TABLE, region_name=TEST_REGION
    )
    session_reader = SessionReader(
        table_name=TEST_SESSIONS_TABLE, region_name=TEST_REGION
    )

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_ingestion_reader] = lambda: ingestion_reader
    app.dependency_overrides[get_summary_reader] = lambda: summary_reader
    app.dependency_overrides[get_session_reader] = lambda: session_reader

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# Re-export for convenience.
make_jwt = _make_token


@pytest.fixture
def ensure_aws_creds() -> Iterator[None]:
    """Helper for tests that initialize boto clients outside `async_client`."""
    if "AWS_ACCESS_KEY_ID" not in os.environ:
        os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    if "AWS_SECRET_ACCESS_KEY" not in os.environ:
        os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    if "AWS_DEFAULT_REGION" not in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = TEST_REGION
    yield
