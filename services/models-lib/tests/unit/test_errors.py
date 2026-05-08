"""Tests for `panakoes_models.errors`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_models.errors import ApiError


@pytest.mark.unit
def test_api_error_minimum_fields() -> None:
    """`code` + `message` are the only required fields."""
    err = ApiError(code="auth.invalid_token", message="Token expired.")
    assert err.code == "auth.invalid_token"
    assert err.message == "Token expired."
    assert err.request_id is None
    assert err.details is None


@pytest.mark.unit
def test_api_error_with_full_payload() -> None:
    """Every optional field round-trips correctly."""
    err = ApiError(
        code="ingestion.too_large",
        message="File exceeds 10 GiB limit.",
        request_id="req_abc",
        details={"size_bytes": 12_000_000_000},
    )
    assert err.request_id == "req_abc"
    assert err.details == {"size_bytes": 12_000_000_000}


@pytest.mark.unit
def test_api_error_rejects_empty_code() -> None:
    """`code` must be non-empty."""
    with pytest.raises(ValidationError):
        ApiError(code="", message="x")


@pytest.mark.unit
def test_api_error_rejects_empty_message() -> None:
    """`message` must be non-empty."""
    with pytest.raises(ValidationError):
        ApiError(code="x", message="")


@pytest.mark.unit
def test_api_error_rejects_extra_fields() -> None:
    """`extra='forbid'` rejects unknown keys."""
    with pytest.raises(ValidationError):
        ApiError(code="x", message="y", surprise="boom")  # type: ignore[call-arg]


@pytest.mark.unit
def test_api_error_round_trips_through_json() -> None:
    """JSON round-trip yields an equal error envelope."""
    original = ApiError(
        code="x.y", message="something", request_id="r", details={"k": 1, "v": [1, 2, 3]}
    )
    raw = original.model_dump_json()
    restored = ApiError.model_validate_json(raw)
    assert restored == original
