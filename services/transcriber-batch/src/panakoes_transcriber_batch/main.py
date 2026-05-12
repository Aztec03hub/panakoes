"""Entrypoint for the transcriber-batch AWS Batch job.

Lifecycle (single Batch invocation):

  1. Load env-derived :class:`Settings`.
  2. Configure structlog + OpenTelemetry instrumentation.
  3. Mark the streaming-sessions row ``active``.
  4. Download the source audio from S3 to a tmpfs path.
  5. Load whisper, transcribe, map to the canonical transcript shape.
  6. Upload the transcript JSON to ``S3_OUTPUT_PREFIX/transcript.json``.
  7. Mark the streaming-sessions row ``completed`` with duration +
     word count + transcript URI.

Failure handling: any exception during steps 4-6 flips the row to
``errored`` with the exception's short message and exits 1. Failure
during step 1-2 exits 1 without touching DDB because we may not have
a usable session id at that point. The Batch service surfaces non-
zero exit codes as job failures, which the CloudWatch FailedJobs
alarm in ``infra/dev/batch/main.tf`` picks up.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from typing import Any

import boto3
import structlog

from panakoes_transcriber_batch.config import Settings, load_settings
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


def run(
    settings: Settings,
    *,
    s3_client: Any | None = None,
    ddb_table: Any | None = None,
    model_loader: Any = load_model,
    transcribe_fn: Any = transcribe,
) -> int:
    """Run a single Batch job to completion.

    The boto3 clients and model loader are injection seams for testing.
    Production callers leave them ``None`` and we instantiate the real
    boto3 clients here.

    Returns the process exit code (0 on success, 1 on failure).
    """
    bound = logger.bind(
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


def main() -> int:
    """Process entrypoint invoked by the container ``CMD``."""
    settings = load_settings()
    _configure_logging(settings.log_level)
    _configure_otel(service_name="transcriber-batch")
    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
