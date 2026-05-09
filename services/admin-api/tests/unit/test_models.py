"""Unit tests for admin-api lifecycle envelope models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from panakoes_admin_api.models import (
    LifecycleRequest,
    LifecycleResponse,
    LifecycleStatus,
)


@pytest.mark.unit
def test_lifecycle_request_requires_non_empty_keys() -> None:
    """`idempotency_key` and `confirmation` cannot be empty strings."""
    with pytest.raises(ValidationError):
        LifecycleRequest(idempotency_key="", confirmation="DRAIN auth")
    with pytest.raises(ValidationError):
        LifecycleRequest(idempotency_key="op-1", confirmation="")


@pytest.mark.unit
def test_lifecycle_request_extra_field_rejected() -> None:
    """Unknown fields raise (frozen + extra=forbid)."""
    with pytest.raises(ValidationError):
        LifecycleRequest(  # type: ignore[call-arg]
            idempotency_key="op-1",
            confirmation="DRAIN auth",
            params={},
            unknown_field="x",
        )


@pytest.mark.unit
def test_lifecycle_response_succeeds_with_terminal_envelope() -> None:
    """A success envelope round-trips JSON cleanly."""
    started_at = datetime.now(UTC)
    envelope = LifecycleResponse(
        idempotency_key="op-1",
        status=LifecycleStatus.SUCCEEDED,
        result={"affected": 1},
        audit_request_id="req-1",
        started_at=started_at,
        finished_at=started_at,
    )
    serialized = envelope.model_dump_json()
    rehydrated = LifecycleResponse.model_validate_json(serialized)
    assert rehydrated == envelope


@pytest.mark.unit
def test_lifecycle_status_str_enum_serializes_to_string() -> None:
    """The status field serializes as a string, not an enum repr."""
    envelope = LifecycleResponse(
        idempotency_key="op-1",
        status=LifecycleStatus.PENDING,
        audit_request_id="req-1",
        started_at=datetime.now(UTC),
    )
    payload = envelope.model_dump()
    assert payload["status"] == "pending"
