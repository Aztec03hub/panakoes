"""Ingestion endpoints: create intent, get one, list paginated.

The S3 event handler that finalizes an ingestion when the upload
completes lives in a separate Lambda (next slice).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from panakoes_audit import record_event

from panakoes_ingestion_api.auth import AuthenticatedUser, get_current_user
from panakoes_ingestion_api.config import Settings
from panakoes_ingestion_api.models import (
    CreateIngestionRequest,
    CreateIngestionResponse,
    IngestionListResponse,
    IngestionRecord,
)
from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.storage.s3 import S3PresignedUrlGenerator, build_object_key

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


def get_settings() -> Settings:
    """FastAPI dependency: build a fresh `Settings` per request.

    Tests override this with a fixture that returns deterministic env
    values; production accepts the cost of re-instantiation.
    """
    return Settings()


def get_ingestion_store(
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestionStore:
    """FastAPI dependency: provide a DynamoDB-backed `IngestionStore`."""
    return IngestionStore(
        table_name=settings.ingestion_table_name,
        region_name=settings.aws_region,
    )


def get_url_generator(
    settings: Annotated[Settings, Depends(get_settings)],
) -> S3PresignedUrlGenerator:
    """FastAPI dependency: provide an S3 pre-signed URL generator."""
    return S3PresignedUrlGenerator(
        bucket=settings.ingestion_bucket,
        region_name=settings.aws_region,
    )


@router.post(
    "/audio",
    response_model=CreateIngestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingestion(
    request: CreateIngestionRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[IngestionStore, Depends(get_ingestion_store)],
    url_generator: Annotated[S3PresignedUrlGenerator, Depends(get_url_generator)],
) -> CreateIngestionResponse:
    """Create an upload intent and return a pre-signed PUT URL."""
    ingestion_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    s3_key = build_object_key(user.user_id, ingestion_id, request.filename)

    record = IngestionRecord(
        ingestion_id=ingestion_id,
        user_id=user.user_id,
        filename=request.filename,
        content_type=request.content_type,
        size_bytes=request.size_bytes,
        s3_key=s3_key,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    store.put(record)

    upload_url = url_generator.generate_put_url(
        key=s3_key,
        content_type=request.content_type,
        size_bytes=request.size_bytes,
        expires_in_seconds=settings.presigned_url_ttl_seconds,
    )
    expires_at = datetime.fromtimestamp(
        now.timestamp() + settings.presigned_url_ttl_seconds, UTC
    )

    await record_event(
        actor_id=user.user_id,
        actor_type="user",
        action="ingestion.intent_created",
        resource_type="ingestion",
        resource_id=ingestion_id,
        source_service="ingestion-api",
        details={
            "filename": request.filename,
            "content_type": request.content_type,
            "size_bytes": request.size_bytes,
        },
    )

    return CreateIngestionResponse(
        ingestion_id=ingestion_id,
        upload_url=upload_url,
        expires_at=expires_at,
    )


@router.get("/{ingestion_id}", response_model=IngestionRecord)
async def get_ingestion(
    ingestion_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: Annotated[IngestionStore, Depends(get_ingestion_store)],
) -> IngestionRecord:
    """Return the caller's ingestion record by id."""
    record = store.get(user.user_id, ingestion_id)
    if record is None:
        # Cross-user access and not-found collapse to the same 404 so
        # we do not leak the existence of other users' records.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ingestion not found",
        )
    return record


@router.get("", response_model=IngestionListResponse)
async def list_ingestions(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[IngestionStore, Depends(get_ingestion_store)],
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> IngestionListResponse:
    """Return the caller's ingestion records, paginated."""
    effective_limit = min(limit, settings.list_max_limit)
    items, next_cursor = store.list_for_user(
        user_id=user.user_id,
        limit=effective_limit,
        cursor=cursor,
    )
    return IngestionListResponse(items=items, next_cursor=next_cursor)
