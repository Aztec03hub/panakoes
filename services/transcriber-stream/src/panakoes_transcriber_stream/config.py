"""Env-driven runtime configuration for ``transcriber-stream``.

The contract (design v7, "Runtime contract") splits env vars into two
groups:

* Required: container fails fast at startup if any is unset.
* Optional: defaults documented inline; container reads but does not
  require.

Each var is one or the other; never both. Tests cover the boundary
behavior so a future agent cannot quietly promote a required var to
optional without breaking the load_config_from_env contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when a required env var is missing or malformed."""


@dataclass(frozen=True, slots=True)
class Config:
    """All env-derived runtime configuration in one immutable bundle."""

    # Required: no default; fail fast if absent.
    session_id: str
    connection_id: str
    frame_queue_url: str
    ws_endpoint: str
    sessions_table: str
    frame_pool_table: str
    transcripts_bucket: str

    # Optional: defaults documented in load_config_from_env() below.
    model_size: str
    model_cache_dir: str
    language_hint: str
    min_chunk_seconds: float
    max_chunk_seconds: float
    idle_seconds_before_exit: float
    keepalive_ping_seconds: float
    buffer_trimming: str
    buffer_trimming_sec: float
    aws_region: str
    log_level: str

    @property
    def model_dir(self) -> str:
        """Absolute path to the AMI-baked CTranslate2 weights directory.

        The startup assertion in ``main`` checks for this directory's
        existence BEFORE the (slow) ``backend_factory`` call so a missing
        AMI bake fails fast with a clear error instead of silently
        falling back to a multi-minute HuggingFace download.
        """

        return f"{self.model_cache_dir}/{self.model_size}-ct2"

    @property
    def warmup_clip_path(self) -> str:
        """Absolute path to the AMI-baked 1 s warmup WAV.

        Adversarial round-5 NIT-03: the warmup clip is part of the AMI
        contract, not something the container fetches from the network.
        """

        return "/opt/whisper/warmup-1s.wav"


def _required(env: dict[str, str], name: str) -> str:
    """Read a required env var; raise ConfigError if missing or empty."""

    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"required env var {name!r} is unset or empty")
    return value


def _optional_float(env: dict[str, str], name: str, default: float) -> float:
    """Read an optional float env var; fall back to default if unset or invalid."""

    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"env var {name!r}={raw!r} is not a valid float") from exc


def load_config_from_env(env: dict[str, str] | None = None) -> Config:
    """Read the canonical env vars and return an immutable ``Config``.

    ``env`` is a seam for unit tests; production callers pass ``None``
    and the function reads from ``os.environ`` directly.
    """

    source = dict(os.environ) if env is None else dict(env)

    return Config(
        session_id=_required(source, "PANAKOES_SESSION_ID"),
        connection_id=_required(source, "PANAKOES_CONNECTION_ID"),
        frame_queue_url=_required(source, "FRAME_QUEUE_URL"),
        ws_endpoint=_required(source, "WS_ENDPOINT"),
        sessions_table=_required(source, "STREAMING_SESSIONS_TABLE"),
        frame_pool_table=_required(source, "STREAMING_FRAME_POOL_TABLE"),
        transcripts_bucket=_required(source, "TRANSCRIPTS_BUCKET"),
        model_size=source.get("MODEL_SIZE", "large-v2").strip() or "large-v2",
        model_cache_dir=(
            source.get("MODEL_CACHE_DIR", "/opt/whisper/models").strip() or "/opt/whisper/models"
        ),
        language_hint=source.get("LANGUAGE_HINT", "en").strip() or "en",
        min_chunk_seconds=_optional_float(source, "MIN_CHUNK_SECONDS", 1.0),
        max_chunk_seconds=_optional_float(source, "MAX_CHUNK_SECONDS", 30.0),
        idle_seconds_before_exit=_optional_float(source, "IDLE_SECONDS_BEFORE_EXIT", 30.0),
        keepalive_ping_seconds=_optional_float(source, "KEEPALIVE_PING_SECONDS", 540.0),
        buffer_trimming=source.get("BUFFER_TRIMMING", "segment").strip() or "segment",
        buffer_trimming_sec=_optional_float(source, "BUFFER_TRIMMING_SEC", 15.0),
        aws_region=source.get("AWS_REGION", "us-east-1").strip() or "us-east-1",
        log_level=source.get("LOG_LEVEL", "INFO").strip() or "INFO",
    )
