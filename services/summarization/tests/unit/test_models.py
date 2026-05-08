"""Unit tests for the summarization API request and response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_summarization.models import SummarizeRequest


@pytest.mark.unit
def test_summarize_request_defaults_to_haiku() -> None:
    """`tier` defaults to `haiku` when omitted by the client."""
    req = SummarizeRequest(transcript_id="t_001")
    assert req.tier == "haiku"


@pytest.mark.unit
def test_summarize_request_accepts_sonnet() -> None:
    """The `sonnet` literal is accepted explicitly."""
    req = SummarizeRequest(transcript_id="t_001", tier="sonnet")
    assert req.tier == "sonnet"


@pytest.mark.unit
def test_summarize_request_rejects_unknown_tier() -> None:
    """Unknown tier strings raise a `ValidationError`."""
    with pytest.raises(ValidationError):
        SummarizeRequest(transcript_id="t_001", tier="opus")  # type: ignore[arg-type]


@pytest.mark.unit
def test_summarize_request_rejects_empty_transcript_id() -> None:
    """An empty `transcript_id` is rejected at the boundary."""
    with pytest.raises(ValidationError):
        SummarizeRequest(transcript_id="")


@pytest.mark.unit
def test_summarize_request_forbids_extra_fields() -> None:
    """Unexpected fields are rejected (`extra='forbid'`)."""
    with pytest.raises(ValidationError):
        SummarizeRequest(transcript_id="t_001", tier="haiku", oops=True)  # type: ignore[call-arg]
