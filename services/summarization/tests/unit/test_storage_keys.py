"""Pure-function tests for the storage key helpers."""

from __future__ import annotations

import pytest

from panakoes_summarization.storage import (
    summary_key,
    summary_pk,
    summary_sk,
    transcript_key,
)


@pytest.mark.unit
def test_transcript_key_is_owner_scoped() -> None:
    """Transcript keys embed the owner so cross-user reads cannot match."""
    assert transcript_key("u1", "t1") == "transcripts/u1/t1.txt"


@pytest.mark.unit
def test_summary_key_is_owner_scoped() -> None:
    """Summary keys embed the owner just like transcript keys."""
    assert summary_key("u1", "t1") == "summaries/u1/t1.md"


@pytest.mark.unit
def test_summary_pk_uses_user_prefix() -> None:
    """The DynamoDB partition key uses the documented `USER#` prefix."""
    assert summary_pk("u1") == "USER#u1"


@pytest.mark.unit
def test_summary_sk_uses_summary_prefix() -> None:
    """The DynamoDB sort key uses the documented `SUMMARY#` prefix."""
    assert summary_sk("t1") == "SUMMARY#t1"
