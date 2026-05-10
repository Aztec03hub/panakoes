"""Unit tests for the transcription orchestration module."""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from panakoes_transcriber import (
    TranscriberAuthError,
    TranscriberRateLimitError,
    TranscriberTimeoutError,
    TranscriptionResult,
    TranscriptionSegment,
    Word,
)
from panakoes_transcriber_groq import GroqTranscriberBackend

from panakoes_ingestion_api.models import IngestionRecord
from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.transcription import (
    get_transcriber,
    transcribe_ingestion,
)
from tests.conftest import TEST_BUCKET_NAME, TEST_REGION, TEST_TABLE_NAME


class _FakeTranscriber:
    """Tiny in-memory `Transcriber` for orchestration tests.

    Conforms to the `Transcriber` Protocol structurally so it can be
    passed wherever a real backend is expected.
    """

    def __init__(self, result: TranscriptionResult | Exception) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        self.calls.append(
            {
                "audio_bytes": audio_bytes,
                "filename": filename,
                "language_hint": language_hint,
            }
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


# ---------------------------------------------------------------------------
# get_transcriber dispatch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_transcriber_defaults_to_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var => groq backend constructed from `GROQ_API_KEY`."""
    monkeypatch.delenv("TRANSCRIBER_BACKEND", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    backend = get_transcriber()
    assert isinstance(backend, GroqTranscriberBackend)


@pytest.mark.unit
def test_get_transcriber_honors_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TRANSCRIBER_BACKEND=groq` selects the Groq backend explicitly."""
    monkeypatch.setenv("TRANSCRIBER_BACKEND", "Groq")  # case-insensitive
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    backend = get_transcriber()
    assert isinstance(backend, GroqTranscriberBackend)


@pytest.mark.unit
def test_get_transcriber_groq_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`GROQ_API_KEY` missing => clear `RuntimeError`."""
    monkeypatch.delenv("TRANSCRIBER_BACKEND", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_transcriber()


@pytest.mark.unit
def test_get_transcriber_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown backend name fails fast with a useful message."""
    monkeypatch.setenv("TRANSCRIBER_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="Unknown TRANSCRIBER_BACKEND"):
        get_transcriber()


@pytest.mark.unit
def test_get_transcriber_openai_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`TRANSCRIBER_BACKEND=openai` without `OPENAI_API_KEY` errors clearly."""
    monkeypatch.setenv("TRANSCRIBER_BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_transcriber()


@pytest.mark.unit
def test_get_transcriber_openai_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`openai` backend selected but its package missing => clear error.

    The `panakoes-transcriber-openai` package is intentionally not a
    hard dependency of the ingestion-api service; only operators
    selecting it pay the install cost.
    """
    monkeypatch.setenv("TRANSCRIBER_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(RuntimeError, match="not installed"):
        get_transcriber()


# ---------------------------------------------------------------------------
# transcribe_ingestion behavior
# ---------------------------------------------------------------------------


def _seed_record_and_object(
    *,
    table: Any,
    s3_client: Any,
    user_id: str = "user_test_123",
    ingestion_id: str = "ing-001",
    audio_bytes: bytes = b"riff-bytes",
    filename: str = "demo.m4a",
) -> IngestionRecord:
    """Create an ingestion row + S3 object and return the seed record."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    record = IngestionRecord(
        ingestion_id=ingestion_id,
        user_id=user_id,
        filename=filename,
        content_type="audio/mp4",
        size_bytes=len(audio_bytes),
        s3_key=f"audio/{user_id}/{ingestion_id}/{filename}",
        status="uploaded",
        created_at=now,
        updated_at=now,
    )
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.put(record)
    s3_client.put_object(Bucket=TEST_BUCKET_NAME, Key=record.s3_key, Body=audio_bytes)
    return record


@pytest.mark.integration
async def test_transcribe_ingestion_happy_path(
    dynamodb_table: Any,
    s3_bucket: str,
) -> None:
    """Successful run persists transcript + flips status to succeeded."""
    s3_client = boto3.client("s3", region_name=TEST_REGION)
    record = _seed_record_and_object(table=dynamodb_table, s3_client=s3_client)

    fake = _FakeTranscriber(
        TranscriptionResult(
            text="hello world",
            segments=(
                TranscriptionSegment(
                    text="hello world",
                    start=0.0,
                    end=1.5,
                    words=(
                        Word(text="hello", start=0.0, end=0.5),
                        Word(text="world", start=0.6, end=1.5),
                    ),
                ),
            ),
            language="en",
            duration_seconds=1.5,
        )
    )
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    await transcribe_ingestion(
        ingestion_id=record.ingestion_id,
        user_id=record.user_id,
        transcriber=fake,
        store=store,
        bucket=TEST_BUCKET_NAME,
        s3_client=s3_client,
    )

    refreshed = store.get(record.user_id, record.ingestion_id)
    assert refreshed is not None
    assert refreshed.transcript_status == "succeeded"
    assert refreshed.transcript is not None
    assert refreshed.transcript.text == "hello world"
    assert refreshed.transcript.language == "en"
    assert refreshed.transcript.duration_seconds == 1.5
    assert len(refreshed.transcript.segments) == 1
    assert len(refreshed.transcript.segments[0].words) == 2
    assert refreshed.transcript_error_message is None
    # The transcriber received the audio bytes and the original filename.
    assert fake.calls[0]["audio_bytes"] == b"riff-bytes"
    assert fake.calls[0]["filename"] == "demo.m4a"


@pytest.mark.integration
@pytest.mark.parametrize(
    "exc",
    [
        TranscriberAuthError("bad creds"),
        TranscriberRateLimitError("slow down", retry_after_seconds=12.0),
        TranscriberTimeoutError("upstream slow"),
    ],
)
async def test_transcribe_ingestion_records_backend_errors(
    dynamodb_table: Any,
    s3_bucket: str,
    exc: Exception,
) -> None:
    """Each Transcriber error type lands status=failed + persisted message."""
    s3_client = boto3.client("s3", region_name=TEST_REGION)
    record = _seed_record_and_object(table=dynamodb_table, s3_client=s3_client)

    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    await transcribe_ingestion(
        ingestion_id=record.ingestion_id,
        user_id=record.user_id,
        transcriber=_FakeTranscriber(exc),
        store=store,
        bucket=TEST_BUCKET_NAME,
        s3_client=s3_client,
    )

    refreshed = store.get(record.user_id, record.ingestion_id)
    assert refreshed is not None
    assert refreshed.transcript_status == "failed"
    assert refreshed.transcript is None
    assert refreshed.transcript_error_message is not None
    assert type(exc).__name__ in refreshed.transcript_error_message


@pytest.mark.integration
async def test_transcribe_ingestion_missing_s3_object(
    dynamodb_table: Any,
    s3_bucket: str,
) -> None:
    """Missing S3 object => status=failed with `audio fetch failed:` prefix."""
    s3_client = boto3.client("s3", region_name=TEST_REGION)
    # Seed the row but skip the put_object call.
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    record = IngestionRecord(
        ingestion_id="ing-missing",
        user_id="user_test_123",
        filename="missing.m4a",
        content_type="audio/mp4",
        size_bytes=1,
        s3_key="audio/user_test_123/ing-missing/missing.m4a",
        status="pending",
        created_at=now,
        updated_at=now,
    )
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.put(record)

    await transcribe_ingestion(
        ingestion_id=record.ingestion_id,
        user_id=record.user_id,
        transcriber=_FakeTranscriber(
            TranscriptionResult(
                text="never called",
                segments=(),
                language=None,
                duration_seconds=None,
            )
        ),
        store=store,
        bucket=TEST_BUCKET_NAME,
        s3_client=s3_client,
    )

    refreshed = store.get(record.user_id, record.ingestion_id)
    assert refreshed is not None
    assert refreshed.transcript_status == "failed"
    assert refreshed.transcript_error_message is not None
    assert "audio fetch failed" in refreshed.transcript_error_message


@pytest.mark.integration
async def test_transcribe_ingestion_record_vanished(
    dynamodb_table: Any,
    s3_bucket: str,
) -> None:
    """Record gone before the background task runs => silent no-op (no crash)."""
    s3_client = boto3.client("s3", region_name=TEST_REGION)
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    fake = _FakeTranscriber(
        TranscriptionResult(text="x", segments=(), language=None, duration_seconds=None)
    )
    # No exception, no persisted transcript.
    await transcribe_ingestion(
        ingestion_id="does-not-exist",
        user_id="user_test_123",
        transcriber=fake,
        store=store,
        bucket=TEST_BUCKET_NAME,
        s3_client=s3_client,
    )
    assert fake.calls == []
