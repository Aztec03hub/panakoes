"""FastAPI entrypoint for the summarization microservice.

Exposes `/health`, `POST /summarize`, `GET /summary/{id}`, and
`GET /summaries`. Every non-health endpoint requires a valid bearer
token; resources are owner-scoped via the JWT `sub` claim.
"""

from __future__ import annotations

import logging
from typing import Annotated

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, status
from panakoes_audit import record_event

from panakoes_summarization import auth
from panakoes_summarization.config import Settings
from panakoes_summarization.models import (
    HealthResponse,
    SummariesPage,
    SummarizeRequest,
    SummaryRecord,
)
from panakoes_summarization.storage import SummaryStorage, TranscriptNotFoundError
from panakoes_summarization.summarizer import Summarizer

settings = Settings()

logging.basicConfig(level=settings.log_level)
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelNamesMapping()[settings.log_level]
    ),
)

logger = structlog.get_logger(__name__)

app = FastAPI(title=f"panakoes-{settings.service_name}")


def get_storage() -> SummaryStorage:
    """Construct the `SummaryStorage` from the active settings.

    Wrapped as a dependency so tests can override it with a moto-backed
    instance via `app.dependency_overrides`.
    """
    return SummaryStorage(
        transcripts_bucket=settings.s3_transcripts_bucket,
        summaries_bucket=settings.s3_summaries_bucket,
        summaries_table=settings.ddb_summaries_table,
        region_name=settings.aws_region,
    )


def get_summarizer() -> Summarizer:
    """Construct the `Summarizer` with the configured Anthropic API key."""
    return Summarizer(api_key=settings.anthropic_api_key or None)


StorageDep = Annotated[SummaryStorage, Depends(get_storage)]
SummarizerDep = Annotated[Summarizer, Depends(get_summarizer)]
UserIdDep = Annotated[str, Depends(auth.get_current_user_id)]


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe, identical to the rest of the Panakoes services."""
    logger.debug("health_check", service=settings.service_name)
    return HealthResponse(status="ok", service=settings.service_name)


@app.post("/summarize", response_model=SummaryRecord)
async def summarize(
    body: SummarizeRequest,
    user_id: UserIdDep,
    storage: StorageDep,
    summarizer: SummarizerDep,
) -> SummaryRecord:
    """Read a transcript, call Claude, persist the summary, and return it."""
    try:
        transcript = storage.read_transcript(user_id, body.transcript_id)
    except TranscriptNotFoundError as exc:
        await record_event(
            actor_id=user_id,
            actor_type="user",
            action="summarization.failed",
            resource_type="transcript",
            resource_id=body.transcript_id,
            source_service=settings.service_name,
            details={"reason": "transcript_not_found", "tier": body.tier},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="transcript not found",
        ) from exc

    try:
        result = summarizer.summarize(transcript, tier=body.tier)
    except Exception:
        await record_event(
            actor_id=user_id,
            actor_type="user",
            action="summarization.failed",
            resource_type="transcript",
            resource_id=body.transcript_id,
            source_service=settings.service_name,
            details={"reason": "anthropic_call_failed", "tier": body.tier},
        )
        raise

    record = storage.write_summary(
        user_id=user_id,
        transcript_id=body.transcript_id,
        summary=result.text,
        tier=body.tier,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    await record_event(
        actor_id=user_id,
        actor_type="user",
        action="summarization.completed",
        resource_type="summary",
        resource_id=body.transcript_id,
        source_service=settings.service_name,
        details={
            "tier": body.tier,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    )
    return record


@app.get("/summary/{transcript_id}", response_model=SummaryRecord)
async def get_summary(
    transcript_id: str,
    user_id: UserIdDep,
    storage: StorageDep,
) -> SummaryRecord:
    """Owner-scoped fetch by transcript id; 404 on miss to avoid leaking existence."""
    record = storage.get_summary(user_id, transcript_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="summary not found",
        )
    return record


@app.get("/summaries", response_model=SummariesPage)
async def list_summaries(
    user_id: UserIdDep,
    storage: StorageDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> SummariesPage:
    """Paginated list of summaries owned by the authenticated user."""
    records, next_cursor = storage.list_summaries(user_id, limit=limit, cursor=cursor)
    return SummariesPage(items=records, next_cursor=next_cursor)


def main() -> None:
    """Run the service with uvicorn for direct invocation."""
    uvicorn.run(
        "panakoes_summarization.main:app",
        host="0.0.0.0",  # noqa: S104  # bind on all interfaces inside the container
        port=8000,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
