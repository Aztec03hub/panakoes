"""Tier 3 lifecycle operation: kill an AWS Batch job.

Calls `batch.terminate_job(jobId=..., reason=...)`. The reason
surfaces in CloudTrail and the AWS Batch console.

Confirmation template: `KILL JOB <job_id>`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from panakoes_admin_api.batch_client import BatchTerminator

logger = structlog.get_logger(__name__)


def make_handler(
    *, terminator: BatchTerminator, job_id: str
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the async handler the orchestrator will invoke."""

    async def handler(params: dict[str, Any]) -> dict[str, Any]:
        reason = str(params.get("reason", "")).strip()
        if not reason:
            raise ValueError("reason is required for kill-batch-job")

        killed_at = datetime.now(UTC).isoformat()
        request_id = terminator.terminate_job(job_id=job_id, reason=reason)

        logger.info(
            "tier3_batch_job_killed",
            job_id=job_id,
            reason=reason,
            request_id=request_id,
        )
        return {
            "job_id": job_id,
            "killed_at": killed_at,
            "killed_reason": reason,
            "batch_terminate_request_id": request_id,
        }

    return handler
