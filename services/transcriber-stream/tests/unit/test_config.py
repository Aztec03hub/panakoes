"""Unit tests for the env-driven Config loader."""

from __future__ import annotations

import pytest

from panakoes_transcriber_stream.config import (
    Config,
    ConfigError,
    load_config_from_env,
)


def test_load_config_required_env_pass(valid_env: dict[str, str]) -> None:
    cfg = load_config_from_env()
    assert cfg.session_id == valid_env["PANAKOES_SESSION_ID"]
    assert cfg.connection_id == valid_env["PANAKOES_CONNECTION_ID"]
    assert cfg.frame_queue_url == valid_env["FRAME_QUEUE_URL"]
    assert cfg.ws_endpoint == valid_env["WS_ENDPOINT"]
    assert cfg.sessions_table == valid_env["STREAMING_SESSIONS_TABLE"]
    assert cfg.frame_pool_table == valid_env["STREAMING_FRAME_POOL_TABLE"]
    assert cfg.transcripts_bucket == valid_env["TRANSCRIPTS_BUCKET"]


def test_load_config_optional_defaults(valid_env: dict[str, str]) -> None:
    cfg = load_config_from_env()
    assert cfg.model_size == "large-v2"
    assert cfg.model_cache_dir == "/opt/whisper/models"
    assert cfg.language_hint == "en"
    assert cfg.min_chunk_seconds == 1.0
    assert cfg.max_chunk_seconds == 30.0
    assert cfg.idle_seconds_before_exit == 30.0
    assert cfg.keepalive_ping_seconds == 540.0
    assert cfg.buffer_trimming == "segment"
    assert cfg.buffer_trimming_sec == 15.0
    assert cfg.aws_region == "us-east-1"
    assert cfg.log_level == "INFO"


def test_load_config_optional_overrides(
    valid_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_SIZE", "large-v3")
    monkeypatch.setenv("LANGUAGE_HINT", "fr")
    monkeypatch.setenv("KEEPALIVE_PING_SECONDS", "120")
    monkeypatch.setenv("MIN_CHUNK_SECONDS", "0.5")
    cfg = load_config_from_env()
    assert cfg.model_size == "large-v3"
    assert cfg.language_hint == "fr"
    assert cfg.keepalive_ping_seconds == 120.0
    assert cfg.min_chunk_seconds == 0.5


def test_load_config_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wipe every required var; load_config_from_env must raise on the first
    # one it sees missing.
    for name in (
        "PANAKOES_SESSION_ID",
        "PANAKOES_CONNECTION_ID",
        "FRAME_QUEUE_URL",
        "WS_ENDPOINT",
        "STREAMING_SESSIONS_TABLE",
        "STREAMING_FRAME_POOL_TABLE",
        "TRANSCRIPTS_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_env(env={})
    # Implementation orders the requireds top-to-bottom; assert the message
    # mentions a required-env-var name (we do not pin the specific one to
    # let future ordering changes pass).
    assert "required env var" in str(excinfo.value)


def test_load_config_invalid_float_raises(
    valid_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEEPALIVE_PING_SECONDS", "not-a-number")
    with pytest.raises(ConfigError) as excinfo:
        load_config_from_env()
    assert "KEEPALIVE_PING_SECONDS" in str(excinfo.value)


def test_load_config_empty_optional_falls_back(
    valid_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_SIZE", "   ")  # whitespace-only
    monkeypatch.setenv("KEEPALIVE_PING_SECONDS", "")
    cfg = load_config_from_env()
    assert cfg.model_size == "large-v2"
    assert cfg.keepalive_ping_seconds == 540.0


def test_config_derived_paths(valid_env: dict[str, str]) -> None:
    cfg = load_config_from_env()
    assert cfg.model_dir == "/opt/whisper/models/large-v2-ct2"
    assert cfg.warmup_clip_path == "/opt/whisper/warmup-1s.wav"


def test_config_is_frozen(valid_env: dict[str, str]) -> None:
    cfg = load_config_from_env()
    # ``frozen=True`` dataclass raises FrozenInstanceError (a dataclasses
    # subclass of AttributeError). We assert the dataclasses-specific
    # type to avoid the B017 "blind exception" lint warning.
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        cfg.session_id = "different"  # type: ignore[misc]


def test_load_config_accepts_explicit_env_dict() -> None:
    env = {
        "PANAKOES_SESSION_ID": "s",
        "PANAKOES_CONNECTION_ID": "c",
        "FRAME_QUEUE_URL": "u",
        "WS_ENDPOINT": "w",
        "STREAMING_SESSIONS_TABLE": "t",
        "STREAMING_FRAME_POOL_TABLE": "p",
        "TRANSCRIPTS_BUCKET": "b",
    }
    cfg = load_config_from_env(env=env)
    assert isinstance(cfg, Config)
    assert cfg.session_id == "s"
