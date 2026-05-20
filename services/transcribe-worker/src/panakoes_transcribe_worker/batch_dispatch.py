"""AWS Batch job submitter for the Whisper-on-GPU async-transcription path.

When ``TRANSCRIBER_BACKEND=batch``, the transcribe-worker Lambda does
NOT call ``transcribe_ingestion`` synchronously (which would route to
Groq/OpenAI). Instead it submits an AWS Batch job that runs the
``panakoes-dev-transcriber-batch`` container on a g4dn.xlarge Spot
instance, downloads the audio from S3, transcribes with Whisper-large-v3
fp16 on the GPU, and writes the transcript onto the ingestion DDB row.

The Lambda returns immediately after Batch accepts the job. The Batch
container is the single writer to the ingestion row's
``transcript_status`` field for this job.

Required env (in addition to the worker's existing config):

  * ``BATCH_JOB_QUEUE`` - name or ARN of the Batch job queue
    (``panakoes-dev-transcribe-queue``).
  * ``BATCH_JOB_DEFINITION`` - name or ARN of the Batch job definition
    (``panakoes-dev-transcribe-batch``).

The job definition's container ``command`` is NOT overridden here; the
container's ``CMD`` already runs the right entrypoint. The job override
sets the env shape the ``run_ingestion_mode`` entrypoint expects.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def submit_batch_job(
    *,
    user_id: str,
    ingestion_id: str,
    s3_input_bucket: str,
    s3_input_key: str,
    ddb_ingestion_table: str,
    job_queue: str | None = None,
    job_definition: str | None = None,
    batch_client: Any | None = None,
) -> str:
    """Submit an AWS Batch job to transcribe ``s3_input_key`` via Whisper-on-GPU.

    Returns the Batch ``jobId``. Raises :class:`RuntimeError` on any
    AWS-side failure so the caller's SQS-batch-item-failure path retries
    via the visibility timeout (the message stays in flight, the worker
    re-tries on next delivery).

    ``ddb_ingestion_table`` is passed via the job's environment overrides
    so the container knows which DDB table to update. Same for
    ``USER_ID`` and ``INGESTION_ID`` which compose the row's partition +
    sort key per the ingestion-api schema.
    """
    if batch_client is None:
        batch_client = boto3.client("batch", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    if job_queue is None:
        job_queue = os.environ.get("BATCH_JOB_QUEUE", "panakoes-dev-transcribe-queue")
    if job_definition is None:
        job_definition = os.environ.get("BATCH_JOB_DEFINITION", "panakoes-dev-transcribe-batch")

    # Job name: alphanumeric + hyphens, 1-128 chars per the AWS contract.
    # `transcribe-<ingestion>` keeps the link readable in the AWS console.
    job_name = f"transcribe-{ingestion_id}"[:128]

    overrides = {
        "environment": [
            {"name": "TARGET_MODE", "value": "ingestion"},
            {"name": "S3_INPUT_BUCKET", "value": s3_input_bucket},
            {"name": "S3_INPUT_KEY", "value": s3_input_key},
            {"name": "INGESTION_ID", "value": ingestion_id},
            {"name": "USER_ID", "value": user_id},
            {"name": "DDB_INGESTION_TABLE", "value": ddb_ingestion_table},
        ]
    }

    try:
        resp = batch_client.submit_job(
            jobName=job_name,
            jobQueue=job_queue,
            jobDefinition=job_definition,
            containerOverrides=overrides,
        )
    except ClientError as exc:
        raise RuntimeError(
            f"batch.submit_job failed for ingestion {ingestion_id}: {exc}"
        ) from exc

    job_id = resp.get("jobId")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(
            f"batch.submit_job returned no jobId for ingestion {ingestion_id}"
        )

    logger.info(
        "transcribe-worker: submitted Batch job",
        extra={
            "ingestion_id": ingestion_id,
            "user_id": user_id,
            "batch_job_id": job_id,
            "job_name": job_name,
        },
    )
    return job_id
