"""Entrypoint for the transcriber-batch AWS Batch job.

Dispatches between two operating modes based on the ``TARGET_MODE`` env var:

  * ``streaming`` (default, legacy): updates the
    ``panakoes-dev-streaming-sessions`` table. Used by the per-session
    streaming-transcription path. Required env: ``S3_INPUT_URI``,
    ``S3_OUTPUT_PREFIX``, ``JOB_ID``, ``SESSION_ID``.

  * ``ingestion``: updates the ``panakoes-dev-ingestion`` table row that
    ``services/ingestion-api`` writes when a user requests a pre-signed
    upload URL. Used by the async file-upload demo path. Required env:
    ``S3_INPUT_BUCKET``, ``S3_INPUT_KEY``, ``INGESTION_ID``, ``USER_ID``,
    ``DDB_INGESTION_TABLE``.

Lifecycle (single Batch invocation, ingestion mode):

  1. Load env-derived :class:`Settings` (or :class:`IngestionSettings`).
  2. Configure structlog + OpenTelemetry instrumentation.
  3. Flip the ingestion row to ``status=uploaded, transcript_status=pending``.
  4. Download the source audio from S3 to a tmpfs path.
  5. Load Whisper-large-v3 fp16, transcribe, map to the canonical
     transcript shape.
  6. Write the transcript onto the ingestion row via UpdateItem.

Failure handling: any exception during steps 4-6 flips the row to
``transcript_status=failed`` with the exception's short message and
exits 1. Failure during steps 1-2 exits 1 without touching DDB because
we may not have a usable id at that point. The Batch service surfaces
non-zero exit codes as job failures, which the CloudWatch FailedJobs
alarm in ``infra/dev/batch/main.tf`` picks up.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

import boto3
import structlog

from panakoes_transcriber_batch.config import Settings, load_settings
from panakoes_transcriber_batch.ingestion import (
    mark_failed as ingestion_mark_failed,
)
from panakoes_transcriber_batch.ingestion import (
    mark_succeeded as ingestion_mark_succeeded,
)
from panakoes_transcriber_batch.ingestion import (
    mark_uploaded as ingestion_mark_uploaded,
)
from panakoes_transcriber_batch.s3 import (
    download_audio,
    upload_transcript_json,
)
from panakoes_transcriber_batch.sessions import (
    mark_completed,
    mark_errored,
    mark_transcribing,
)
from panakoes_transcriber_batch.transcribe import (
    TranscriptionFailedError,
    WhisperLoadError,
    load_model,
    transcribe,
)

logger = structlog.get_logger(__name__)


def _configure_logging(log_level: str) -> None:
    """Wire stdlib logging + structlog to the configured level."""
    logging.basicConfig(level=log_level, stream=sys.stdout, format="%(message)s")
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
        ),
    )


def _configure_otel(service_name: str) -> None:
    """Configure panakoes-otel.

    Failures are logged but never re-raised: observability must not
    take down a successful transcription job.
    """
    try:
        from panakoes_otel import configure as otel_configure
        from panakoes_otel import instrument_boto3

        otel_configure(
            service_name=service_name,
            environment=os.getenv("DEPLOYMENT_ENVIRONMENT", "dev"),
        )
        instrument_boto3()
    except Exception:
        logger.warning("transcriber_batch_otel_configure_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Ingestion-mode entrypoint
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestionSettings:
    """Env-driven config for ingestion mode."""

    s3_input_bucket: str
    s3_input_key: str
    ingestion_id: str
    user_id: str
    ddb_ingestion_table: str
    aws_region: str
    model_path: str
    device: str
    log_level: str


def _load_ingestion_settings() -> IngestionSettings:
    """Read ingestion-mode env vars. Raise on any missing required value."""

    def required(name: str) -> str:
        v = os.environ.get(name, "")
        if not v:
            raise RuntimeError(f"{name} env var is required in TARGET_MODE=ingestion")
        return v

    return IngestionSettings(
        s3_input_bucket=required("S3_INPUT_BUCKET"),
        s3_input_key=required("S3_INPUT_KEY"),
        ingestion_id=required("INGESTION_ID"),
        user_id=required("USER_ID"),
        ddb_ingestion_table=required("DDB_INGESTION_TABLE"),
        aws_region=os.environ.get("AWS_REGION", "us-east-1"),
        model_path=os.environ.get("MODEL_PATH", "/opt/whisper/models/large-v3.pt"),
        device=os.environ.get("DEVICE", "cuda"),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )


def run_ingestion_mode(
    settings: IngestionSettings,
    *,
    s3_client: Any | None = None,
    ddb_table: Any | None = None,
    model_loader: Any = load_model,
    transcribe_fn: Any = transcribe,
) -> int:
    """Run a single ingestion-mode Batch job to completion.

    Same overall shape as :func:`run`, but writes to the
    ``panakoes-dev-ingestion`` table instead of streaming-sessions.
    Returns 0 on success, 1 on failure.
    """
    bound = logger.bind(
        mode="ingestion",
        ingestion_id=settings.ingestion_id,
        user_id=settings.user_id,
        s3_input=f"s3://{settings.s3_input_bucket}/{settings.s3_input_key}",
    )
    bound.info("transcriber_batch_ingestion_job_start")

    if s3_client is None:
        s3_client = boto3.client("s3", region_name=settings.aws_region)
    if ddb_table is None:
        ddb_resource = boto3.resource("dynamodb", region_name=settings.aws_region)
        ddb_table = ddb_resource.Table(settings.ddb_ingestion_table)

    try:
        ingestion_mark_uploaded(
            ddb_table,
            user_id=settings.user_id,
            ingestion_id=settings.ingestion_id,
        )
    except Exception:
        bound.exception("transcriber_batch_ingestion_mark_uploaded_failed")
        # Cannot even mark the row; downstream state would be inconsistent.
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="panakoes-batch-") as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.bin")
            input_uri = f"s3://{settings.s3_input_bucket}/{settings.s3_input_key}"
            download_audio(s3_client, input_uri, audio_path)
            bound.info("transcriber_batch_audio_downloaded", local_path=audio_path)

            model = model_loader(settings.model_path, device=settings.device)
            transcript = transcribe_fn(model, audio_path)
            bound.info(
                "transcriber_batch_transcript_ready",
                word_count=transcript.get("word_count"),
                duration_seconds=transcript.get("duration_seconds"),
            )

            ingestion_mark_succeeded(
                ddb_table,
                user_id=settings.user_id,
                ingestion_id=settings.ingestion_id,
                transcript=transcript,
            )
            bound.info("transcriber_batch_ingestion_job_complete")
            return 0
    except WhisperLoadError as exc:
        bound.exception("transcriber_batch_model_load_failed")
        _safe_mark_ingestion_failed(ddb_table, settings, f"model load failed: {exc}", bound)
        return 1
    except TranscriptionFailedError as exc:
        bound.exception("transcriber_batch_transcription_failed")
        _safe_mark_ingestion_failed(ddb_table, settings, f"transcription failed: {exc}", bound)
        return 1
    except Exception as exc:
        bound.exception("transcriber_batch_unexpected_failure")
        _safe_mark_ingestion_failed(ddb_table, settings, f"unexpected failure: {exc}", bound)
        return 1


def _safe_mark_ingestion_failed(
    ddb_table: Any,
    settings: IngestionSettings,
    message: str,
    bound_logger: Any,
) -> None:
    """Update the ingestion DDB row to failed, swallowing secondary failures."""
    try:
        ingestion_mark_failed(
            ddb_table,
            user_id=settings.user_id,
            ingestion_id=settings.ingestion_id,
            error_message=message,
        )
    except Exception:
        bound_logger.exception("transcriber_batch_ingestion_mark_failed_failed")


# ---------------------------------------------------------------------------
# Streaming-mode entrypoint (existing behavior)
# ---------------------------------------------------------------------------


def run(
    settings: Settings,
    *,
    s3_client: Any | None = None,
    ddb_table: Any | None = None,
    model_loader: Any = load_model,
    transcribe_fn: Any = transcribe,
) -> int:
    """Run a single Batch job to completion (streaming mode).

    The boto3 clients and model loader are injection seams for testing.
    Production callers leave them ``None`` and we instantiate the real
    boto3 clients here.

    Returns the process exit code (0 on success, 1 on failure).
    """
    bound = logger.bind(
        mode="streaming",
        job_id=settings.job_id,
        session_id=settings.session_id,
        s3_input_uri=settings.s3_input_uri,
    )
    bound.info("transcriber_batch_job_start")

    if s3_client is None:
        s3_client = boto3.client("s3", region_name=settings.aws_region)
    if ddb_table is None:
        ddb_resource = boto3.resource("dynamodb", region_name=settings.aws_region)
        ddb_table = ddb_resource.Table(settings.sessions_table)

    try:
        mark_transcribing(ddb_table, settings.session_id)
    except Exception:
        bound.exception("transcriber_batch_session_mark_active_failed")
        # If we cannot even mark the row, downstream state will be
        # inconsistent; bail before doing the expensive GPU work.
        return 1

    try:
        with tempfile.TemporaryDirectory(prefix="panakoes-batch-") as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.bin")
            download_audio(s3_client, settings.s3_input_uri, audio_path)
            bound.info("transcriber_batch_audio_downloaded", local_path=audio_path)

            model = model_loader(settings.model_path, device=settings.device)
            transcript = transcribe_fn(model, audio_path)

            output_uri = upload_transcript_json(
                s3_client,
                settings.s3_output_prefix,
                transcript,
            )
            bound.info(
                "transcriber_batch_transcript_uploaded",
                transcript_uri=output_uri.uri,
            )

            mark_completed(
                ddb_table,
                settings.session_id,
                transcript_uri=output_uri.uri,
                duration_seconds=float(transcript.get("duration_seconds") or 0.0),
                word_count=int(transcript.get("word_count") or 0),
            )
            bound.info("transcriber_batch_job_complete")
            return 0
    except WhisperLoadError as exc:
        bound.exception("transcriber_batch_model_load_failed")
        _safe_mark_errored(ddb_table, settings.session_id, f"model load failed: {exc}", bound)
        return 1
    except TranscriptionFailedError as exc:
        bound.exception("transcriber_batch_transcription_failed")
        _safe_mark_errored(ddb_table, settings.session_id, f"transcription failed: {exc}", bound)
        return 1
    except Exception as exc:
        bound.exception("transcriber_batch_unexpected_failure")
        _safe_mark_errored(ddb_table, settings.session_id, f"unexpected failure: {exc}", bound)
        return 1


def _safe_mark_errored(
    ddb_table: Any,
    session_id: str,
    message: str,
    bound_logger: Any,
) -> None:
    """Update the DDB row to errored, swallowing failures.

    If marking errored itself fails we still need to exit nonzero;
    swallowing the secondary exception keeps the primary error in the
    log and lets the main flow return its own exit code.
    """
    try:
        mark_errored(ddb_table, session_id, error_message=message)
    except Exception:
        bound_logger.exception("transcriber_batch_session_mark_errored_failed")


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


def main() -> int:
    """Process entrypoint invoked by the container ``CMD``.

    Dispatches based on the ``TARGET_MODE`` env var:

      * ``ingestion`` -> :func:`run_ingestion_mode` updates the
        ingestion-api DDB table (async file-upload demo path).
      * ``streaming`` (default) -> :func:`run` updates the
        streaming-sessions DDB table (the existing per-session path).
    """
    target_mode = os.environ.get("TARGET_MODE", "streaming").strip().lower()
    if target_mode == "ingestion":
        settings_ingestion = _load_ingestion_settings()
        _configure_logging(settings_ingestion.log_level)
        _configure_otel(service_name="transcriber-batch")
        return run_ingestion_mode(settings_ingestion)

    # Default: streaming mode (existing behavior).
    settings = load_settings()
    _configure_logging(settings.log_level)
    _configure_otel(service_name="transcriber-batch")
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
