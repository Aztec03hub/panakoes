"""Unit tests for `panakoes_test_helpers.factories`.

These tests are written to pass whether or not `panakoes_models` is
installed. When the models package IS installed, factories return real
Pydantic instances and we assert against `.id` / `.email` etc. When the
package is NOT installed, factories return dicts and we assert against
keys instead. The shared accessor helper at the top of this module
encapsulates the difference.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from panakoes_test_helpers import factories
from panakoes_test_helpers.factories import (
    make_ingestion_record,
    make_streaming_session,
    make_summary,
    make_user,
)


def _get(obj: Any, name: str) -> Any:
    """Read `name` from a dict via key, or from a model via attribute.

    Lets every assertion below work regardless of whether
    `panakoes_models` is present in the test environment.
    """
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


@pytest.mark.unit
def test_make_user_default_id_starts_with_user_prefix() -> None:
    """The generated id matches the documented `user_<token>` shape."""
    user = make_user()
    assert _get(user, "id").startswith("user_")


@pytest.mark.unit
def test_make_user_default_email_is_panakoes_test() -> None:
    """The default email lives on the test domain so production routing is safe."""
    user = make_user()
    assert _get(user, "email").endswith("@panakoes.test")


@pytest.mark.unit
def test_make_user_default_tier_is_free() -> None:
    """Free is the default tier, matching the open-signup landing state."""
    user = make_user()
    assert _get(user, "tier") == "free"


@pytest.mark.unit
def test_make_user_default_role_is_user() -> None:
    """Role defaults to `user` (admin must be opt-in via override)."""
    user = make_user()
    assert _get(user, "role") == "user"


@pytest.mark.unit
def test_make_user_overrides_apply() -> None:
    """Caller-supplied keys win over defaults."""
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    user = make_user(
        id="user_explicitlyset12345",
        email="alice@panakoes.test",
        tier="pro",
        role="admin",
        created_at=fixed,
    )
    assert _get(user, "id") == "user_explicitlyset12345"
    assert _get(user, "email") == "alice@panakoes.test"
    assert _get(user, "tier") == "pro"
    assert _get(user, "role") == "admin"
    assert _get(user, "created_at") == fixed


@pytest.mark.unit
def test_make_user_default_created_at_is_utc() -> None:
    """Timestamps are timezone-aware (UTC), matching every project model."""
    user = make_user()
    created = _get(user, "created_at")
    assert isinstance(created, datetime)
    assert created.tzinfo is not None


@pytest.mark.unit
def test_make_user_two_calls_produce_distinct_ids() -> None:
    """Defaults randomise so back-to-back factories never collide."""
    a = make_user()
    b = make_user()
    assert _get(a, "id") != _get(b, "id")


@pytest.mark.unit
def test_make_ingestion_record_default_status_is_pending() -> None:
    """A freshly-created ingestion is `pending` by default."""
    rec = make_ingestion_record()
    status = _get(rec, "status")
    # StrEnum compares equal to its string value, so this works either way.
    assert status == "pending"


@pytest.mark.unit
def test_make_ingestion_record_default_content_type_is_audio_wav() -> None:
    """Audio/wav is the canonical default upload type."""
    rec = make_ingestion_record()
    assert _get(rec, "content_type") == "audio/wav"


@pytest.mark.unit
def test_make_ingestion_record_id_starts_with_ingest_prefix() -> None:
    """The default id matches `ingest_<token>` shape."""
    rec = make_ingestion_record()
    assert _get(rec, "id").startswith("ingest_")


@pytest.mark.unit
def test_make_ingestion_record_s3_key_includes_id() -> None:
    """`s3_key` derives from the ingestion id; pairs scale together."""
    rec = make_ingestion_record()
    assert _get(rec, "id") in _get(rec, "s3_key")


@pytest.mark.unit
def test_make_ingestion_record_overrides_apply() -> None:
    """Caller can pin any field, including status."""
    rec = make_ingestion_record(
        status="uploaded",
        filename="custom.mp3",
        content_type="audio/mpeg",
        size_bytes=42,
    )
    assert _get(rec, "status") == "uploaded"
    assert _get(rec, "filename") == "custom.mp3"
    assert _get(rec, "content_type") == "audio/mpeg"
    assert _get(rec, "size_bytes") == 42


@pytest.mark.unit
def test_make_summary_default_tier_is_haiku() -> None:
    """Haiku is the cost-disciplined default summary tier."""
    summary = make_summary()
    tier = _get(summary, "tier")
    assert tier == "haiku"


@pytest.mark.unit
def test_make_summary_default_model_is_haiku() -> None:
    """Default `model` matches the project's locked Claude Haiku 4.5 choice."""
    summary = make_summary()
    assert _get(summary, "model") == "claude-haiku-4-5"


@pytest.mark.unit
def test_make_summary_id_prefix() -> None:
    """The id matches the `sum_<token>` documented shape."""
    summary = make_summary()
    assert _get(summary, "id").startswith("sum_")


@pytest.mark.unit
def test_make_summary_overrides_apply() -> None:
    """Token counts and tier can be pinned for billing-path tests."""
    summary = make_summary(tier="sonnet", prompt_tokens=10_000, completion_tokens=2_000)
    assert _get(summary, "tier") == "sonnet"
    assert _get(summary, "prompt_tokens") == 10_000
    assert _get(summary, "completion_tokens") == 2_000


@pytest.mark.unit
def test_make_streaming_session_default_status_is_starting() -> None:
    """A fresh session starts in `starting`, awaiting GPU bind."""
    session = make_streaming_session()
    status = _get(session, "status")
    assert status == "starting"


@pytest.mark.unit
def test_make_streaming_session_default_gpu_instance_is_none() -> None:
    """No GPU bound until the spot instance is acquired."""
    session = make_streaming_session()
    assert _get(session, "gpu_instance_id") is None


@pytest.mark.unit
def test_make_streaming_session_id_prefix() -> None:
    """The id matches the `sess_<token>` documented shape."""
    session = make_streaming_session()
    assert _get(session, "id").startswith("sess_")


@pytest.mark.unit
def test_make_streaming_session_overrides_apply() -> None:
    """Status, GPU id, and TTL can be pinned for lifecycle-path tests."""
    session = make_streaming_session(
        status="active",
        gpu_instance_id="i-0123456789abcdef0",
        ttl=1_762_500_000,
    )
    assert _get(session, "status") == "active"
    assert _get(session, "gpu_instance_id") == "i-0123456789abcdef0"
    assert _get(session, "ttl") == 1_762_500_000


@pytest.mark.unit
def test_factories_return_models_when_panakoes_models_available() -> None:
    """When the models lib is installed, factories return Pydantic instances; otherwise dicts.

    This test asserts the right outcome for whichever environment is
    active so it stays green in both modes (CI runs without the models
    lib, an integrated checkout runs with it).
    """
    user = make_user()
    if factories._HAS_PANAKOES_MODELS:
        from panakoes_models import User

        assert isinstance(user, User)
    else:
        assert isinstance(user, dict)


@pytest.mark.unit
def test_factories_degrade_to_dict_when_models_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forcing the `_HAS_PANAKOES_MODELS` flag false makes every factory return a plain dict."""
    monkeypatch.setattr(factories, "_HAS_PANAKOES_MODELS", False)
    assert isinstance(make_user(), dict)
    assert isinstance(make_ingestion_record(), dict)
    assert isinstance(make_summary(), dict)
    assert isinstance(make_streaming_session(), dict)
