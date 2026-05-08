"""Tests for `panakoes_models.summaries`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from panakoes_models.summaries import Summary, SummaryTier

VALID_SUMMARY: dict[str, object] = {
    "id": "sum_abcd1234efgh5678",
    "transcript_id": "tx_abcd1234efgh5678",
    "user_id": "user_abcdefghijkl",
    "tier": SummaryTier.HAIKU,
    "text": "A pithy summary.",
    "model": "claude-haiku-4.5",
    "created_at": datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    "prompt_tokens": 1024,
    "completion_tokens": 256,
}


@pytest.mark.unit
def test_summary_constructs() -> None:
    """A canonical valid Summary constructs cleanly."""
    s = Summary(**VALID_SUMMARY)  # type: ignore[arg-type]
    assert s.id == "sum_abcd1234efgh5678"
    assert s.tier is SummaryTier.HAIKU


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "sum_",
        "sum_short",
        "sum_abcd1234efgh567",  # 15 chars
        "SUM_abcd1234efgh5678",
        "summary_abcd1234efgh5678",
    ],
)
def test_summary_rejects_invalid_id(bad_id: str) -> None:
    """The summary id regex rejects malformed identifiers."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "id": bad_id})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_invalid_transcript_id() -> None:
    """A malformed transcript id is rejected."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "transcript_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_invalid_user_id() -> None:
    """A malformed user id is rejected."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "user_id": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("tier", list(SummaryTier))
def test_summary_accepts_every_tier(tier: SummaryTier) -> None:
    """Every documented tier value is accepted."""
    s = Summary(**{**VALID_SUMMARY, "tier": tier})  # type: ignore[arg-type]
    assert s.tier is tier


@pytest.mark.unit
def test_summary_rejects_unknown_tier() -> None:
    """An undocumented tier is rejected."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "tier": "opus"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_negative_prompt_tokens() -> None:
    """`prompt_tokens` must be non-negative."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "prompt_tokens": -1})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_negative_completion_tokens() -> None:
    """`completion_tokens` must be non-negative."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "completion_tokens": -1})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_empty_model() -> None:
    """Empty `model` is rejected."""
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "model": ""})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_rejects_naive_created_at() -> None:
    """A naive `created_at` raises a validation error."""
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        Summary(**{**VALID_SUMMARY, "created_at": naive})  # type: ignore[arg-type]


@pytest.mark.unit
def test_summary_round_trips_through_json() -> None:
    """JSON round-trip yields an equal summary."""
    original = Summary(**VALID_SUMMARY)  # type: ignore[arg-type]
    raw = original.model_dump_json()
    restored = Summary.model_validate_json(raw)
    assert restored == original
