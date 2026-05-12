"""Unit tests for the env-driven Settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from panakoes_transcriber_batch.config import Settings, load_settings

pytestmark = pytest.mark.unit


def test_load_settings_reads_all_required_env(valid_settings_env: dict[str, str]) -> None:
    settings = load_settings()
    assert settings.s3_input_uri == valid_settings_env["S3_INPUT_URI"]
    assert settings.s3_output_prefix == valid_settings_env["S3_OUTPUT_PREFIX"]
    assert settings.job_id == valid_settings_env["JOB_ID"]
    assert settings.session_id == valid_settings_env["SESSION_ID"]
    assert settings.sessions_table == valid_settings_env["SESSIONS_TABLE"]
    assert settings.model_path == valid_settings_env["MODEL_PATH"]
    assert settings.device == valid_settings_env["DEVICE"]


def test_missing_required_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("S3_INPUT_URI", "S3_OUTPUT_PREFIX", "JOB_ID", "SESSION_ID"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_defaults_are_dev_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S3_INPUT_URI", "s3://b/k")
    monkeypatch.setenv("S3_OUTPUT_PREFIX", "s3://b/p")
    monkeypatch.setenv("JOB_ID", "j")
    monkeypatch.setenv("SESSION_ID", "s")
    # Don't set the optional fields so we exercise the defaults.
    for key in ("MODEL_PATH", "SESSIONS_TABLE", "AWS_REGION", "LOG_LEVEL", "DEVICE"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.model_path == "/opt/whisper/models/large-v3.pt"
    assert settings.sessions_table == "panakoes-dev-streaming-sessions"
    assert settings.aws_region == "us-east-1"
    assert settings.log_level == "INFO"
    assert settings.device == "cuda"
