"""Pydantic and state-machine tests for the Session Manager models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_session_manager.models import (
    ALLOWED_TRANSITIONS,
    CreateSessionRequest,
    SessionStatus,
    UpdateSessionRequest,
    is_allowed_transition,
    is_terminal,
)


@pytest.mark.unit
def test_create_session_request_defaults_language_to_english() -> None:
    """Empty body parses with `language='en'`."""
    body = CreateSessionRequest()
    assert body.language == "en"


@pytest.mark.unit
def test_create_session_request_accepts_other_language_codes() -> None:
    """Other ISO codes (validated downstream) parse fine."""
    assert CreateSessionRequest(language="es").language == "es"
    assert CreateSessionRequest(language="ja").language == "ja"


@pytest.mark.unit
def test_create_session_request_rejects_too_short_language() -> None:
    """A 1-char language code is rejected."""
    with pytest.raises(ValidationError):
        CreateSessionRequest(language="x")


@pytest.mark.unit
def test_create_session_request_rejects_extra_fields() -> None:
    """Extra fields are rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate({"language": "en", "user_id": "spoofed"})


@pytest.mark.unit
def test_update_session_request_accepts_status_only() -> None:
    """A status-only update parses."""
    body = UpdateSessionRequest(status="active")
    assert body.status == "active"
    assert body.gpu_instance_id is None


@pytest.mark.unit
def test_update_session_request_accepts_gpu_instance_only() -> None:
    """A gpu-instance-only update parses."""
    body = UpdateSessionRequest(gpu_instance_id="i-0abcdef1234567890")
    assert body.status is None
    assert body.gpu_instance_id == "i-0abcdef1234567890"


@pytest.mark.unit
def test_update_session_request_rejects_invalid_status() -> None:
    """Unknown status values are rejected."""
    with pytest.raises(ValidationError):
        UpdateSessionRequest.model_validate({"status": "nope"})


@pytest.mark.unit
def test_update_session_request_rejects_extra_fields() -> None:
    """Extra fields are rejected."""
    with pytest.raises(ValidationError):
        UpdateSessionRequest.model_validate({"status": "active", "user_id": "x"})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("from_status", "to_status", "allowed"),
    [
        ("starting", "active", True),
        ("starting", "errored", True),
        ("starting", "paused", False),
        ("starting", "completed", False),
        ("active", "paused", True),
        ("active", "completed", True),
        ("active", "errored", True),
        ("active", "starting", False),
        ("paused", "active", True),
        ("paused", "completed", True),
        ("paused", "errored", True),
        ("paused", "starting", False),
        ("completed", "active", False),
        ("completed", "errored", False),
        ("errored", "active", False),
        ("errored", "completed", False),
    ],
)
def test_is_allowed_transition_matrix(
    from_status: SessionStatus,
    to_status: SessionStatus,
    allowed: bool,
) -> None:
    """Every row in the transition matrix is enforced."""
    assert is_allowed_transition(from_status, to_status) is allowed


@pytest.mark.unit
def test_is_terminal_for_each_status() -> None:
    """Only `completed` and `errored` are terminal."""
    assert is_terminal("completed") is True
    assert is_terminal("errored") is True
    assert is_terminal("starting") is False
    assert is_terminal("active") is False
    assert is_terminal("paused") is False


@pytest.mark.unit
def test_allowed_transitions_table_covers_every_status() -> None:
    """Every status has an entry in `ALLOWED_TRANSITIONS` (no missing keys)."""
    for status in ("starting", "active", "paused", "completed", "errored"):
        assert status in ALLOWED_TRANSITIONS
