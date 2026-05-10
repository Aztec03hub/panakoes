"""Thin AWS Batch wrapper used by the kill-batch-job lifecycle op.

Wraps `aws_batch.terminate_job` so tests can inject a fake without
booting moto's batch backend. Mirrors the EventBridge wrapper's shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import structlog

if TYPE_CHECKING:
    from mypy_boto3_batch.client import BatchClient as Boto3BatchClient


logger = structlog.get_logger(__name__)


class BatchTerminator(Protocol):
    """Protocol satisfied by both production and fake terminators."""

    def terminate_job(self, *, job_id: str, reason: str) -> str:
        """Terminate the given job. Returns the boto3 request id (for audit)."""
        ...


class Boto3BatchTerminator:
    """Production AWS Batch terminator backed by boto3."""

    def __init__(self, *, client: Boto3BatchClient) -> None:
        self._client = client

    def terminate_job(self, *, job_id: str, reason: str) -> str:
        """Terminate a Batch job. The reason surfaces in CloudTrail."""
        response = self._client.terminate_job(jobId=job_id, reason=reason)
        request_id = response.get("ResponseMetadata", {}).get("RequestId", "")
        logger.info(
            "tier3_batch_terminate_job",
            job_id=job_id,
            reason=reason,
            request_id=request_id,
        )
        return str(request_id)
