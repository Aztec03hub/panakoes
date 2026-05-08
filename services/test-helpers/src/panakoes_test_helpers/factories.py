"""Factory helpers that produce Panakoes domain objects for tests.

Every Panakoes service tests against `panakoes_models` types: `User`,
`IngestionRecord`, `Summary`, `StreamingSession`. Constructing one in a
test file means filling in 6-10 fields with sensible defaults, and the
defaults are nearly identical across files. This module collects the
defaults in one place.

## Graceful degradation

If `panakoes_models` is installed in the consuming environment, each
factory returns a real Pydantic instance. If it is not (for example,
when a service runs `panakoes-test-helpers` standalone), each factory
returns a plain `dict` with the same shape. The graceful-degradation
path means this library has no hard dependency on the models package,
which keeps the dependency graph clean and lets the test-helpers ship
ahead of the models lib in a fresh checkout.

## Why hand-rolled instead of factory-boy

`factory-boy` is the obvious choice, but its API is built around class
inheritance and a separate `Faker`-driven random generator. For the
four shapes Panakoes cares about, a flat function with `**overrides`
is shorter, has no third-party surface for typing to fight with, and
keeps the helper signatures self-documenting.

## ULID identifiers

Panakoes uses ULID-shaped identifiers (`user_<26 chars>`,
`ingest_<26 chars>`, etc.) so default ids carry sortable timestamps
without leaking entropy. We generate them with `secrets.token_hex` and
prefix per type; the result satisfies every model regex.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from importlib import util as _importlib_util
from typing import Any

# Detect panakoes_models availability without importing it eagerly. The
# import would otherwise fail in environments that legitimately do not
# have the models lib installed, which is the whole point of the
# graceful-degradation path.
_HAS_PANAKOES_MODELS: bool = _importlib_util.find_spec("panakoes_models") is not None


def _new_id(prefix: str, *, length: int = 16) -> str:
    """Build a `<prefix>_<token>` identifier of the right shape.

    The Panakoes model regexes accept any alphanumeric token of at
    least the documented minimum length. `secrets.token_hex(length)`
    yields `2*length` hex characters; `length=16` produces 32 chars,
    well past every minimum, with no collision risk in practice.
    """
    return f"{prefix}_{secrets.token_hex(length)}"


def _utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC `datetime`."""
    return datetime.now(UTC)


def _random_email() -> str:
    """Generate a unique-enough test email for `User` defaults."""
    return f"user-{secrets.token_hex(6)}@panakoes.test"


def _build(
    payload: dict[str, Any],
    overrides: dict[str, Any],
    model_module: str,
    model_class: str,
) -> Any:
    """Apply `overrides` to `payload` and instantiate the model if available.

    Centralising this logic keeps every factory consistent: each one
    builds its default dict, merges caller overrides on top, and either
    returns the dict (no `panakoes_models`) or constructs the
    corresponding Pydantic model (models installed). This shape is the
    seam that makes graceful-degradation possible.
    """
    merged: dict[str, Any] = {**payload, **overrides}
    if not _HAS_PANAKOES_MODELS:
        return merged
    # Lazy import: only imported when the package is actually present.
    module = __import__(model_module, fromlist=[model_class])
    model = getattr(module, model_class)
    return model(**merged)


def make_user(**overrides: Any) -> Any:
    """Build a `User`-shaped object with sensible defaults.

    Defaults: random `id` (`user_<32 hex>`), random `@panakoes.test`
    email, `created_at=now`, `role="user"`, `tier="free"`. Override
    any field by passing it as a keyword.
    """
    payload: dict[str, Any] = {
        "id": _new_id("user"),
        "email": _random_email(),
        "created_at": _utcnow(),
        "role": "user",
        "tier": "free",
    }
    return _build(payload, overrides, "panakoes_models.users", "User")


def make_ingestion_record(**overrides: Any) -> Any:
    """Build an `IngestionRecord`-shaped object with sensible defaults.

    Defaults capture a freshly-uploaded WAV file: `status="pending"`,
    `content_type="audio/wav"`, `size_bytes=1_048_576` (1 MiB). The
    `s3_key` is derived from the generated `id` so it never collides
    across factories called inside the same test.
    """
    now = _utcnow()
    ingestion_id = _new_id("ingest")
    payload: dict[str, Any] = {
        "id": ingestion_id,
        "user_id": _new_id("user"),
        "filename": "recording.wav",
        "content_type": "audio/wav",
        "size_bytes": 1_048_576,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "s3_key": f"uploads/{ingestion_id}.wav",
        "error": None,
    }
    return _build(payload, overrides, "panakoes_models.ingestion", "IngestionRecord")


def make_summary(**overrides: Any) -> Any:
    """Build a `Summary`-shaped object with sensible defaults.

    Defaults reflect the cost-disciplined Haiku tier: `tier="haiku"`,
    `model="claude-haiku-4-5"`, modest token counts. `transcript_id`
    and `user_id` are auto-generated; pass them explicitly when a test
    needs a summary tied to a specific ingestion or user.
    """
    payload: dict[str, Any] = {
        "id": _new_id("sum"),
        "transcript_id": _new_id("trnscr"),
        "user_id": _new_id("user"),
        "tier": "haiku",
        "text": "Test summary text.",
        "model": "claude-haiku-4-5",
        "created_at": _utcnow(),
        "prompt_tokens": 512,
        "completion_tokens": 128,
    }
    return _build(payload, overrides, "panakoes_models.summaries", "Summary")


def make_streaming_session(**overrides: Any) -> Any:
    """Build a `StreamingSession`-shaped object with sensible defaults.

    Defaults represent a session in the `starting` state with no GPU
    bound yet, which is the most common starting point for tests that
    drive the lifecycle forward. `ttl` is left `None` so tests that
    care about DDB TTL set it explicitly.
    """
    now = _utcnow()
    payload: dict[str, Any] = {
        "id": _new_id("sess"),
        "user_id": _new_id("user"),
        "status": "starting",
        "gpu_instance_id": None,
        "created_at": now,
        "updated_at": now,
        "ttl": None,
    }
    return _build(payload, overrides, "panakoes_models.sessions", "StreamingSession")
