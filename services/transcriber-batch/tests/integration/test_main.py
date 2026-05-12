"""Integration tests for ``main.run`` against moto S3 + DynamoDB.

Whisper is mocked at the seam (``model_loader`` + ``transcribe_fn``
injection points on ``run``) so the test never touches a GPU or the
openai-whisper wheel. The full S3 download + transcript upload + DDB
update flow is exercised end-to-end.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
import pytest

from panakoes_transcriber_batch.config import load_settings
from panakoes_transcriber_batch.main import run
from panakoes_transcriber_batch.transcribe import (
    TranscriptionFailedError,
    WhisperLoadError,
)

pytestmark = pytest.mark.integration


class _FakeModel:
    """Stand-in for a loaded whisper model."""


def _ok_transcribe_fn(result: dict[str, Any]) -> Any:
    def _call(model: Any, audio_path: str) -> dict[str, Any]:
        return result
    return _call


def _canonical(fake_whisper_result: dict[str, Any]) -> dict[str, Any]:
    """Produce a canonical-shape transcript from the fake whisper result."""
    from panakoes_transcriber_batch.transcribe import _to_canonical

    return _to_canonical(fake_whisper_result)


def test_run_writes_transcript_and_marks_completed(
    s3_client: object,
    sessions_table: object,
    audio_object: str,
    fake_whisper_result: dict[str, Any],
) -> None:
    settings = load_settings()
    canonical = _canonical(fake_whisper_result)
    exit_code = run(
        settings,
        s3_client=s3_client,
        ddb_table=sessions_table,
        model_loader=lambda path, device: _FakeModel(),
        transcribe_fn=_ok_transcribe_fn(canonical),
    )
    assert exit_code == 0

    # Transcript object landed at the canonical key.
    body = s3_client.get_object(  # type: ignore[attr-defined]
        Bucket="panakoes-dev-transcripts",
        Key="sess_test1234567890ab/transcript.json",
    )["Body"].read()
    transcript = json.loads(body)
    assert transcript["text"] == "hello world this is a test"
    assert transcript["word_count"] == 6

    # DDB row reflects completion.
    item = sessions_table.get_item(Key={"id": settings.session_id})["Item"]  # type: ignore[attr-defined]
    assert item["status"] == "completed"
    assert item["word_count"] == 6
    assert item["transcript_uri"].endswith("/transcript.json")


def test_run_marks_errored_on_transcription_failure(
    s3_client: object,
    sessions_table: object,
    audio_object: str,
) -> None:
    settings = load_settings()

    def _boom(model: Any, audio_path: str) -> dict[str, Any]:
        raise TranscriptionFailedError("simulated")

    exit_code = run(
        settings,
        s3_client=s3_client,
        ddb_table=sessions_table,
        model_loader=lambda path, device: _FakeModel(),
        transcribe_fn=_boom,
    )
    assert exit_code == 1
    item = sessions_table.get_item(Key={"id": settings.session_id})["Item"]  # type: ignore[attr-defined]
    assert item["status"] == "errored"
    assert "transcription failed" in item["error_message"]


def test_run_marks_errored_on_model_load_failure(
    s3_client: object,
    sessions_table: object,
    audio_object: str,
) -> None:
    settings = load_settings()

    def _bad_loader(path: str, device: str) -> Any:
        raise WhisperLoadError("simulated missing weights")

    exit_code = run(
        settings,
        s3_client=s3_client,
        ddb_table=sessions_table,
        model_loader=_bad_loader,
        transcribe_fn=_ok_transcribe_fn({}),
    )
    assert exit_code == 1
    item = sessions_table.get_item(Key={"id": settings.session_id})["Item"]  # type: ignore[attr-defined]
    assert item["status"] == "errored"
    assert "model load failed" in item["error_message"]


def test_run_marks_errored_on_unexpected_exception(
    s3_client: object,
    sessions_table: object,
    audio_object: str,
) -> None:
    settings = load_settings()

    def _surprise(model: Any, audio_path: str) -> dict[str, Any]:
        raise ValueError("unrelated")

    exit_code = run(
        settings,
        s3_client=s3_client,
        ddb_table=sessions_table,
        model_loader=lambda path, device: _FakeModel(),
        transcribe_fn=_surprise,
    )
    assert exit_code == 1
    item = sessions_table.get_item(Key={"id": settings.session_id})["Item"]  # type: ignore[attr-defined]
    assert item["status"] == "errored"


def test_run_returns_1_when_session_row_update_fails(
    s3_client: object,
    audio_object: str,
) -> None:
    settings = load_settings()
    # No table exists in moto, so the first UpdateItem fails.
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    missing_table = ddb.Table("does-not-exist")

    exit_code = run(
        settings,
        s3_client=s3_client,
        ddb_table=missing_table,
        model_loader=lambda path, device: _FakeModel(),
        transcribe_fn=_ok_transcribe_fn({}),
    )
    assert exit_code == 1
