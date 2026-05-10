"""On-demand transcription route for ingestion records.

For now this route is invoked manually (or by a smoke test). Follow-up:
wire S3 ObjectCreated events through EventBridge -> SQS so a worker
fires this code path automatically once an upload completes. That
follow-up does not change this module's contract; it only adds another
caller.

Per the orchestration model in ADR-009, the route stays thin: it
handles auth + idempotency + validation, then schedules
`transcribe_ingestion` on `BackgroundTasks` and returns 202
immediately so the user-facing latency stays predictable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from panakoes_transcriber import Transcriber

from panakoes_ingestion_api.auth import AuthenticatedUser, get_current_user
from panakoes_ingestion_api.config import Settings
from panakoes_ingestion_api.models import IngestionRecord
from panakoes_ingestion_api.routes.ingestion import (
    get_ingestion_store,
    get_settings,
)
from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.transcription import (
    get_transcriber,
    transcribe_ingestion,
)

router = APIRouter(prefix="/api/v1/transcribe", tags=["transcribe"])


def get_transcriber_dep() -> Transcriber:
    """FastAPI dependency wrapper around the env-var dispatch.

    Tests override this to inject a fake transcriber.
    """
    return get_transcriber()


@router.post(
    "/{ingestion_id}",
    response_model=IngestionRecord,
)
async def request_transcription(
    ingestion_id: str,
    background_tasks: BackgroundTasks,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[IngestionStore, Depends(get_ingestion_store)],
    transcriber: Annotated[Transcriber, Depends(get_transcriber_dep)],
) -> IngestionRecord:
    """Request a transcription for an uploaded ingestion record.

    Idempotency:
    - status `succeeded` -> 200 with the existing record (no new run).
    - status `pending`   -> 202 (already queued/running).
    - status `failed` or absent -> 202 + schedule a (re)run.

    Cross-user access collapses to 404 to match the existing
    `GET /ingestion/{id}` pattern (do not leak the existence of other
    users' records).
    """
    record = store.get(user.user_id, ingestion_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion not found",
        )

    if record.transcript_status == "succeeded":
        # Already done; skip the re-run and surface the persisted
        # transcript so the caller does not need a separate GET.
        return record

    if record.transcript_status == "pending":
        # Already queued or in flight; do not double-schedule. The
        # response shape is the same so the client polls the same way.
        return record

    # Either no transcript yet, or the previous attempt failed and the
    # caller is retrying. Mark pending now so a concurrent request
    # short-circuits at the branch above; then schedule the work.
    store.set_transcript_pending(user.user_id, ingestion_id)
    background_tasks.add_task(
        transcribe_ingestion,
        ingestion_id=ingestion_id,
        user_id=user.user_id,
        transcriber=transcriber,
        store=store,
        bucket=settings.ingestion_bucket,
        region_name=settings.aws_region,
    )

    refreshed = store.get(user.user_id, ingestion_id)
    # The pending update we just issued lands before this read returns,
    # so `refreshed` is non-None; the assert keeps mypy quiet without
    # adding a runtime branch on an impossible state.
    assert refreshed is not None
    return refreshed
