"""Unit tests for the streaming-sessions DDB row update helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from panakoes_transcriber_batch.sessions import (
    mark_completed,
    mark_errored,
    mark_transcribing,
)

pytestmark = pytest.mark.unit

SESSION_ID = "sess_test1234567890ab"


def _read(table: object) -> dict[str, object]:
    return table.get_item(Key={"id": SESSION_ID})["Item"]  # type: ignore[attr-defined]


def test_mark_transcribing_sets_status_active(sessions_table: object) -> None:
    mark_transcribing(sessions_table, SESSION_ID)
    item = _read(sessions_table)
    assert item["status"] == "active"
    assert "updated_at" in item


def test_mark_completed_writes_metrics(sessions_table: object) -> None:
    mark_completed(
        sessions_table,
        SESSION_ID,
        transcript_uri="s3://panakoes-dev-transcripts/sess_test1234567890ab/transcript.json",
        duration_seconds=3.2,
        word_count=6,
    )
    item = _read(sessions_table)
    assert item["status"] == "completed"
    assert item["transcript_uri"].endswith("/transcript.json")
    assert item["word_count"] == 6
    assert item["duration_seconds"] == Decimal("3.2")


def test_mark_errored_truncates_long_message(sessions_table: object) -> None:
    long_msg = "x" * 1000
    mark_errored(sessions_table, SESSION_ID, error_message=long_msg)
    item = _read(sessions_table)
    assert item["status"] == "errored"
    assert len(item["error_message"]) == 500


def test_status_transitions_overwrite(sessions_table: object) -> None:
    mark_transcribing(sessions_table, SESSION_ID)
    mark_completed(
        sessions_table,
        SESSION_ID,
        transcript_uri="s3://b/k",
        duration_seconds=1.0,
        word_count=1,
    )
    assert _read(sessions_table)["status"] == "completed"
