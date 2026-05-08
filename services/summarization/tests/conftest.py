"""Shared pytest fixtures for the summarization service.

Provides:

- a clean env-var baseline so `Settings()` is reproducible across tests
- a moto-backed AWS context with the buckets and table the service expects
- a fake Anthropic summarizer so unit and integration tests never hit
  the live Claude API
- a fully wired `httpx.AsyncClient` against the FastAPI app, with
  storage and summarizer overrides already in place
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import boto3
import jwt
import pytest
import pytest_asyncio
from moto import mock_aws
from panakoes_audit import MemoryAuditStore
from panakoes_audit._api import reset_store, set_store

if TYPE_CHECKING:
    from httpx import AsyncClient

    from panakoes_summarization.storage import SummaryStorage
    from panakoes_summarization.summarizer import Summarizer

JWT_SECRET = "test-secret-do-not-use-in-prod-x" * 2
JWT_ISSUER = "panakoes-auth"
JWT_AUDIENCE = "panakoes"

TRANSCRIPTS_BUCKET = "panakoes-test-transcripts"
SUMMARIES_BUCKET = "panakoes-test-summaries"
SUMMARIES_TABLE = "panakoes-test-summaries"


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the env vars the service reads at import time."""
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("S3_TRANSCRIPTS_BUCKET", TRANSCRIPTS_BUCKET)
    monkeypatch.setenv("S3_SUMMARIES_BUCKET", SUMMARIES_BUCKET)
    monkeypatch.setenv("DDB_SUMMARIES_TABLE", SUMMARIES_TABLE)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    # moto credentials so boto3 does not try the IAM metadata service
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # keep audit events out of the way during tests
    monkeypatch.setenv("AUDIT_BACKEND", "memory")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Apply the standard test env vars and yield."""
    _set_env(monkeypatch)
    yield


@pytest.fixture
def audit_store() -> Iterator[MemoryAuditStore]:
    """Install a `MemoryAuditStore` so handlers' audit calls are inspectable."""
    store = MemoryAuditStore()
    set_store(store)
    yield store
    reset_store()


@pytest.fixture
def aws(env: None) -> Iterator[None]:
    """Activate moto and provision the buckets + table the service expects."""
    _ = env
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=TRANSCRIPTS_BUCKET)
        s3.create_bucket(Bucket=SUMMARIES_BUCKET)
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=SUMMARIES_TABLE,
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
        yield


@pytest.fixture
def storage(aws: None) -> SummaryStorage:
    """Return a freshly constructed `SummaryStorage` bound to the moto AWS."""
    _ = aws
    from panakoes_summarization.storage import SummaryStorage

    return SummaryStorage(
        transcripts_bucket=TRANSCRIPTS_BUCKET,
        summaries_bucket=SUMMARIES_BUCKET,
        summaries_table=SUMMARIES_TABLE,
        region_name="us-east-1",
    )


@dataclass
class _FakeUsage:
    """Minimal stand-in for `anthropic.types.Usage`."""

    input_tokens: int
    output_tokens: int


@dataclass
class _FakeBlock:
    """Minimal stand-in for an Anthropic content block."""

    type: str
    text: str


@dataclass
class _FakeResponse:
    """Minimal stand-in for `anthropic.types.Message`."""

    content: list[_FakeBlock]
    usage: _FakeUsage


class FakeMessages:
    """Fake `client.messages` that records calls and returns canned responses."""

    def __init__(self, response_text: str = "## Summary\n\nA brief summary.") -> None:
        """Configure the canned summary text returned by `create()`."""
        self.calls: list[dict[str, object]] = []
        self.response_text = response_text

    def create(self, **kwargs: object) -> _FakeResponse:
        """Record the kwargs and return a `_FakeResponse` with canned text."""
        self.calls.append(kwargs)
        return _FakeResponse(
            content=[_FakeBlock(type="text", text=self.response_text)],
            usage=_FakeUsage(input_tokens=120, output_tokens=45),
        )


class FakeAnthropicClient:
    """Fake Anthropic SDK client exposing a `messages` attribute."""

    def __init__(self, response_text: str = "## Summary\n\nA brief summary.") -> None:
        """Build a fake client wired up with a fresh `FakeMessages`."""
        self.messages = FakeMessages(response_text=response_text)


@pytest.fixture
def fake_anthropic() -> FakeAnthropicClient:
    """Provide a fresh fake Anthropic client per test."""
    return FakeAnthropicClient()


@pytest.fixture
def summarizer(fake_anthropic: FakeAnthropicClient) -> Summarizer:
    """Return a `Summarizer` bound to the fake Anthropic client."""
    from panakoes_summarization.summarizer import Summarizer

    return Summarizer(client=fake_anthropic)


def make_token(
    *,
    sub: str = "user_test_001",
    issuer: str = JWT_ISSUER,
    audience: str = JWT_AUDIENCE,
    secret: str = JWT_SECRET,
    expires_in: int = 3600,
    extra_claims: dict[str, object] | None = None,
) -> str:
    """Mint a signed JWT for tests."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "jti": "session_test_001",
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def token_factory() -> object:
    """Expose `make_token` as a fixture for parametric token construction."""
    return make_token


@pytest_asyncio.fixture
async def async_client(
    aws: None,
    summarizer: Summarizer,
    audit_store: MemoryAuditStore,
) -> AsyncIterator[AsyncClient]:
    """Build an httpx async client wired into the FastAPI app.

    Forces a fresh import of `panakoes_summarization.main` so the
    module-level `Settings()` picks up the test env vars provided by
    the `aws` fixture chain.
    """
    _ = aws, audit_store
    from httpx import ASGITransport, AsyncClient

    main_module = importlib.import_module("panakoes_summarization.main")
    main_module = importlib.reload(main_module)
    app = main_module.app

    from panakoes_summarization.storage import SummaryStorage

    test_storage = SummaryStorage(
        transcripts_bucket=TRANSCRIPTS_BUCKET,
        summaries_bucket=SUMMARIES_BUCKET,
        summaries_table=SUMMARIES_TABLE,
        region_name="us-east-1",
    )
    app.dependency_overrides[main_module.get_storage] = lambda: test_storage
    app.dependency_overrides[main_module.get_summarizer] = lambda: summarizer
    # Ensure the auth dependency reads our test secret rather than whatever
    # leaked into the module-level `settings` from the prior import.
    from panakoes_summarization import auth as auth_module
    from panakoes_summarization.config import Settings

    def _test_settings() -> Settings:
        return Settings()

    app.dependency_overrides[auth_module.get_settings] = _test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


def seed_transcript(user_id: str, transcript_id: str, body: str) -> None:
    """Write a transcript object to the moto-backed S3 bucket."""
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(
        Bucket=TRANSCRIPTS_BUCKET,
        Key=f"transcripts/{user_id}/{transcript_id}.txt",
        Body=body.encode("utf-8"),
    )
