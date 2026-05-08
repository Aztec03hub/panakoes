"""moto-backed integration tests for `SummaryStorage`."""

from __future__ import annotations

import pytest

from panakoes_summarization.storage import (
    SummaryStorage,
    TranscriptNotFoundError,
)
from tests.conftest import seed_transcript


@pytest.mark.integration
def test_read_transcript_returns_seeded_body(storage: SummaryStorage) -> None:
    """A transcript previously written to S3 round-trips to text."""
    seed_transcript("u1", "t1", "the body")
    assert storage.read_transcript("u1", "t1") == "the body"


@pytest.mark.integration
def test_read_transcript_missing_raises_typed_error(storage: SummaryStorage) -> None:
    """A missing key raises `TranscriptNotFoundError` for the handler to catch."""
    with pytest.raises(TranscriptNotFoundError):
        storage.read_transcript("u1", "missing")


@pytest.mark.integration
def test_read_transcript_isolates_users(storage: SummaryStorage) -> None:
    """User A cannot read user B's transcript even if the id collides."""
    seed_transcript("user_a", "shared_id", "secret")
    with pytest.raises(TranscriptNotFoundError):
        storage.read_transcript("user_b", "shared_id")


@pytest.mark.integration
def test_write_summary_persists_s3_and_dynamo(storage: SummaryStorage) -> None:
    """A successful summary write lands in both S3 and DynamoDB."""
    record = storage.write_summary(
        user_id="u1",
        transcript_id="t1",
        summary="## Summary\n\nText.",
        tier="haiku",
        model="claude-haiku-4-5",
        input_tokens=100,
        output_tokens=50,
    )
    assert record.s3_key == "summaries/u1/t1.md"
    fetched = storage.get_summary("u1", "t1")
    assert fetched is not None
    assert fetched.summary == "## Summary\n\nText."
    assert fetched.tier == "haiku"
    assert fetched.model == "claude-haiku-4-5"
    assert fetched.input_tokens == 100
    assert fetched.output_tokens == 50


@pytest.mark.integration
def test_get_summary_returns_none_for_missing(storage: SummaryStorage) -> None:
    """A miss returns `None` so the handler maps it to a 404."""
    assert storage.get_summary("u1", "nope") is None


@pytest.mark.integration
def test_get_summary_isolates_users(storage: SummaryStorage) -> None:
    """User A cannot see user B's summary metadata even with a known id."""
    storage.write_summary(
        user_id="user_a",
        transcript_id="shared",
        summary="A's body",
        tier="haiku",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
    )
    assert storage.get_summary("user_b", "shared") is None


@pytest.mark.integration
def test_list_summaries_paginates(storage: SummaryStorage) -> None:
    """A second page is reachable via the returned cursor."""
    for i in range(5):
        storage.write_summary(
            user_id="u1",
            transcript_id=f"t{i:02d}",
            summary=f"summary {i}",
            tier="haiku",
            model="claude-haiku-4-5",
            input_tokens=1,
            output_tokens=1,
        )
    page1, cursor = storage.list_summaries("u1", limit=2)
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = storage.list_summaries("u1", limit=2, cursor=cursor)
    assert len(page2) == 2
    seen = {r.transcript_id for r in [*page1, *page2]}
    assert len(seen) == 4  # all distinct
    assert cursor2 is not None  # one more record left
    page3, cursor3 = storage.list_summaries("u1", limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None


@pytest.mark.integration
def test_list_summaries_isolates_users(storage: SummaryStorage) -> None:
    """The list query is keyed on the user, so users never see each other."""
    storage.write_summary(
        user_id="user_a",
        transcript_id="t1",
        summary="A",
        tier="haiku",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
    )
    storage.write_summary(
        user_id="user_b",
        transcript_id="t2",
        summary="B",
        tier="haiku",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
    )
    page, _ = storage.list_summaries("user_a")
    assert len(page) == 1
    assert page[0].transcript_id == "t1"


@pytest.mark.integration
def test_storage_exposes_constructor_arguments(storage: SummaryStorage) -> None:
    """The configured bucket and table names are accessible for diagnostics."""
    assert storage.transcripts_bucket
    assert storage.summaries_bucket
    assert storage.summaries_table
