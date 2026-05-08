"""Schema-level checks for the Query API response models.

Light coverage: the records are simple wrappers around DynamoDB
attributes, but we still want to catch accidental schema drift early.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from panakoes_query_api.models import (
    HealthResponse,
    IngestionListResponse,
    IngestionRecord,
    SessionListResponse,
    SessionRecord,
    SummaryListResponse,
    SummaryRecord,
)


@pytest.mark.unit
def test_health_response_round_trips() -> None:
    """`HealthResponse` serialises to the expected payload."""
    body = HealthResponse(status="ok", service="query")
    assert body.model_dump() == {"status": "ok", "service": "query"}


@pytest.mark.unit
def test_ingestion_record_validates_required_fields() -> None:
    """All required fields must be present on `IngestionRecord`."""
    now = datetime.now(UTC)
    record = IngestionRecord(
        ingestion_id="i1",
        user_id="u1",
        filename="a.m4a",
        content_type="audio/mp4",
        size_bytes=1,
        s3_key="audio/u1/i1/a.m4a",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    assert record.ingestion_id == "i1"


@pytest.mark.unit
def test_ingestion_record_rejects_unknown_status() -> None:
    """Status is constrained to the documented Literal values."""
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        IngestionRecord(
            ingestion_id="i1",
            user_id="u1",
            filename="a.m4a",
            content_type="audio/mp4",
            size_bytes=1,
            s3_key="audio/u1/i1/a.m4a",
            status="bogus",  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )


@pytest.mark.unit
def test_summary_record_accepts_fast_and_deep_tiers() -> None:
    """Summary tier accepts both `fast` and `deep`."""
    now = datetime.now(UTC)
    fast = SummaryRecord(
        transcript_id="t1",
        user_id="u1",
        summary_text="x",
        tier="fast",
        model="claude-haiku-4-5",
        created_at=now,
        updated_at=now,
    )
    deep = SummaryRecord(
        transcript_id="t2",
        user_id="u1",
        summary_text="x",
        tier="deep",
        model="claude-sonnet-4-6",
        created_at=now,
        updated_at=now,
    )
    assert fast.tier == "fast"
    assert deep.tier == "deep"


@pytest.mark.unit
def test_session_record_allows_optional_ended_at() -> None:
    """`ended_at` defaults to None for active sessions."""
    now = datetime.now(UTC)
    record = SessionRecord(
        session_id="s1",
        user_id="u1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    assert record.ended_at is None


@pytest.mark.unit
def test_list_responses_default_to_empty_lists() -> None:
    """Empty list responses serialise the no-cursor case correctly."""
    assert IngestionListResponse(items=[]).next_cursor is None
    assert SummaryListResponse(items=[]).next_cursor is None
    assert SessionListResponse(items=[]).next_cursor is None
