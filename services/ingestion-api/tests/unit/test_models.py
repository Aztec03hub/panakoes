"""Pydantic validation tests for the ingestion request/response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_ingestion_api.models import CreateIngestionRequest


@pytest.mark.unit
def test_create_ingestion_request_accepts_valid_payload() -> None:
    """A correctly-shaped payload validates."""
    body = CreateIngestionRequest(
        filename="meeting.m4a",
        content_type="audio/mp4",
        size_bytes=1_024_000,
    )
    assert body.filename == "meeting.m4a"


@pytest.mark.unit
def test_create_ingestion_request_rejects_zero_size() -> None:
    """A zero-byte upload is rejected."""
    with pytest.raises(ValidationError):
        CreateIngestionRequest(filename="x.m4a", content_type="audio/mp4", size_bytes=0)


@pytest.mark.unit
def test_create_ingestion_request_rejects_huge_size() -> None:
    """A 100GB upload is rejected by the upper bound."""
    with pytest.raises(ValidationError):
        CreateIngestionRequest(
            filename="x.m4a",
            content_type="audio/mp4",
            size_bytes=100 * 1024 * 1024 * 1024,
        )


@pytest.mark.unit
def test_create_ingestion_request_rejects_empty_filename() -> None:
    """Empty filename is rejected."""
    with pytest.raises(ValidationError):
        CreateIngestionRequest(filename="", content_type="audio/mp4", size_bytes=10)


@pytest.mark.unit
def test_create_ingestion_request_rejects_extra_fields() -> None:
    """Unexpected fields are rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        CreateIngestionRequest.model_validate(
            {
                "filename": "x.m4a",
                "content_type": "audio/mp4",
                "size_bytes": 10,
                "user_id": "should-not-be-set-by-client",
            }
        )
