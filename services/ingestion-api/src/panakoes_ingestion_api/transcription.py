"""Transcription orchestration for the Ingestion API.

The Transcriber abstraction lives in `services/transcriber-lib/`
(`panakoes_transcriber`); concrete backends live in their own packages
(`panakoes_transcriber_groq`, planned: `panakoes_transcriber_openai`,
`panakoes_transcriber_whisper_gpu`).

This module sits at the seam between the abstraction and the
ingestion service: it picks the right backend by env var, fetches the
audio bytes from S3, hands them to the backend, and persists the
result onto the existing ingestion DynamoDB row.

Per ADR-009, call sites depend on the `Transcriber` Protocol rather
than any concrete backend, so swapping backends is configuration. The
env-var dispatch happens once at construction time; downstream code
treats every backend as a `Transcriber`.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, cast

import boto3
from panakoes_transcriber import (
    Transcriber,
    TranscriberError,
    TranscriptionResult,
)
from panakoes_transcriber_groq import GroqTranscriberBackend

from panakoes_ingestion_api.models import (
    TranscriptModel,
    TranscriptSegmentModel,
    TranscriptWordModel,
)
from panakoes_ingestion_api.storage.dynamodb import IngestionStore

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client

logger = logging.getLogger(__name__)

DEFAULT_BACKEND = "groq"


def _to_transcript_model(result: TranscriptionResult) -> TranscriptModel:
    """Convert the shared dataclass result into the Pydantic mirror."""
    segments = [
        TranscriptSegmentModel(
            text=segment.text,
            start=segment.start,
            end=segment.end,
            words=[
                TranscriptWordModel(text=word.text, start=word.start, end=word.end)
                for word in segment.words
            ],
        )
        for segment in result.segments
    ]
    return TranscriptModel(
        text=result.text,
        segments=segments,
        language=result.language,
        duration_seconds=result.duration_seconds,
    )


def get_transcriber() -> Transcriber:
    """Return a `Transcriber` instance selected by the `TRANSCRIBER_BACKEND` env var.

    Defaults to ``groq``. The corresponding API key env var must be set;
    a missing key raises a clear `RuntimeError` so the operator hits a
    useful message rather than a backend's internal validation error.

    Adding a new backend means adding a branch here AND adding the
    backend's package as a dependency in `pyproject.toml`. Tests cover
    the dispatch logic so a typo in `TRANSCRIBER_BACKEND` fails fast.
    """
    backend_name = os.getenv("TRANSCRIBER_BACKEND", DEFAULT_BACKEND).strip().lower()
    if backend_name == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "TRANSCRIBER_BACKEND=groq but GROQ_API_KEY is not set; "
                "set it to your Groq API key before requesting transcription."
            )
        return GroqTranscriberBackend(api_key=api_key)
    if backend_name == "openai":
        # Placeholder for the planned `panakoes-transcriber-openai` backend.
        # We import lazily so the openai package is not a hard dependency of
        # this service; only operators selecting the openai backend pay the
        # install cost. The `RuntimeError` keeps the dispatch failure local
        # rather than letting `ImportError` bubble up unhelpfully.
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "TRANSCRIBER_BACKEND=openai but OPENAI_API_KEY is not set; "
                "set it to your OpenAI API key before requesting transcription."
            )
        try:
            from panakoes_transcriber_openai import (  # type: ignore[import-not-found]
                OpenAITranscriberBackend,
            )
        except ImportError as exc:
            raise RuntimeError(
                "TRANSCRIBER_BACKEND=openai but the panakoes-transcriber-openai "
                "package is not installed in this service."
            ) from exc
        return cast(Transcriber, OpenAITranscriberBackend(api_key=api_key))
    raise RuntimeError(
        f"Unknown TRANSCRIBER_BACKEND={backend_name!r}; "
        "supported values are 'groq', 'openai'."
    )


def _build_s3_client(region_name: str) -> S3Client:
    """Construct a boto3 S3 client. Split out so tests can monkeypatch."""
    client: S3Client = boto3.client("s3", region_name=region_name)
    return client


async def transcribe_ingestion(
    *,
    ingestion_id: str,
    user_id: str,
    transcriber: Transcriber,
    store: IngestionStore,
    bucket: str,
    s3_client: S3Client | None = None,
    region_name: str = "us-east-1",
) -> None:
    """Transcribe an uploaded ingestion and persist the result.

    Reads the audio bytes from S3 using the ingestion record's S3 key,
    calls the transcriber, and writes the result back to the DynamoDB
    row via UpdateItem. On any `TranscriberError` (or missing-S3-object
    / unexpected error), flips `transcript_status` to ``failed`` with a
    short operator-facing error message; the row still exists, so the
    front-end can surface "transcription failed" without losing the
    upload.

    The function is designed to run inside a FastAPI `BackgroundTasks`
    wrapper so the HTTP request returns 202 immediately.
    """
    record = store.get(user_id, ingestion_id)
    if record is None:
        # The schedulng route already validated the record exists; this
        # branch defends against a race where the record is deleted
        # between scheduling and execution. Nothing to update; log and
        # exit.
        logger.warning(
            "transcribe_ingestion: record vanished",
            extra={"ingestion_id": ingestion_id, "user_id": user_id},
        )
        return

    client = s3_client if s3_client is not None else _build_s3_client(region_name)

    try:
        response = client.get_object(Bucket=bucket, Key=record.s3_key)
        audio_bytes = response["Body"].read()
    except Exception as exc:  # any S3 failure is a terminal "failed"
        logger.exception(
            "transcribe_ingestion: failed to read audio from S3",
            extra={"ingestion_id": ingestion_id, "s3_key": record.s3_key},
        )
        store.set_transcript_failed(
            user_id=user_id,
            ingestion_id=ingestion_id,
            error_message=f"audio fetch failed: {exc}",
        )
        return

    try:
        result = await transcriber.transcribe(
            audio_bytes=audio_bytes,
            filename=record.filename,
        )
    except TranscriberError as exc:
        logger.warning(
            "transcribe_ingestion: backend error",
            extra={
                "ingestion_id": ingestion_id,
                "error_type": type(exc).__name__,
            },
        )
        store.set_transcript_failed(
            user_id=user_id,
            ingestion_id=ingestion_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return
    except Exception as exc:  # avoid silent crash in BackgroundTasks
        logger.exception(
            "transcribe_ingestion: unexpected error",
            extra={"ingestion_id": ingestion_id},
        )
        store.set_transcript_failed(
            user_id=user_id,
            ingestion_id=ingestion_id,
            error_message=f"unexpected error: {exc}",
        )
        return

    transcript_model = _to_transcript_model(result)
    store.set_transcript_succeeded(
        user_id=user_id,
        ingestion_id=ingestion_id,
        transcript=transcript_model,
    )
