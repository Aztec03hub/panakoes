"""Verify the `summarization.failed` audit event fires when Claude raises."""

from __future__ import annotations

import importlib

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from panakoes_audit import MemoryAuditStore

from tests.conftest import (
    SUMMARIES_BUCKET,
    SUMMARIES_TABLE,
    TRANSCRIPTS_BUCKET,
    make_token,
    seed_transcript,
)


class _BoomMessages:
    """Fake Anthropic messages client that always raises."""

    def create(self, **kwargs: object) -> object:
        """Simulate a downstream Claude API failure."""
        raise RuntimeError("anthropic blew up")


class _BoomClient:
    """Fake Anthropic client wired to the failing messages object."""

    def __init__(self) -> None:
        """Initialize with the failing `messages` attribute."""
        self.messages = _BoomMessages()


@pytest_asyncio.fixture
async def failing_client(
    aws: None,
    audit_store: MemoryAuditStore,
):
    """Build an httpx client where the Summarizer always raises."""
    _ = aws, audit_store
    main_module = importlib.import_module("panakoes_summarization.main")
    main_module = importlib.reload(main_module)
    app = main_module.app

    from panakoes_summarization.storage import SummaryStorage
    from panakoes_summarization.summarizer import Summarizer

    test_storage = SummaryStorage(
        transcripts_bucket=TRANSCRIPTS_BUCKET,
        summaries_bucket=SUMMARIES_BUCKET,
        summaries_table=SUMMARIES_TABLE,
        region_name="us-east-1",
    )
    failing_summarizer = Summarizer(client=_BoomClient())
    app.dependency_overrides[main_module.get_storage] = lambda: test_storage
    app.dependency_overrides[main_module.get_summarizer] = lambda: failing_summarizer
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_summarize_records_failure_when_claude_raises(
    failing_client: AsyncClient,
    audit_store: MemoryAuditStore,
) -> None:
    """A Claude exception is recorded as `summarization.failed` before it re-raises.

    The ASGI transport propagates server exceptions to the test caller
    rather than masking them as a 500, which is the framework default
    when no exception middleware is configured. The contract this test
    cares about is the audit-event emission, not the wire-level status,
    so we catch the re-raise and assert on the audit log.
    """
    seed_transcript("user_z", "tx_boom", "body")
    token = make_token(sub="user_z")
    with pytest.raises(RuntimeError):
        await failing_client.post(
            "/summarize",
            headers={"Authorization": f"Bearer {token}"},
            json={"transcript_id": "tx_boom", "tier": "haiku"},
        )
    failure_events = [e for e in audit_store.events if e.action == "summarization.failed"]
    assert len(failure_events) == 1
    assert failure_events[0].details["reason"] == "anthropic_call_failed"
