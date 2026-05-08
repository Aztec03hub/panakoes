"""Unit tests for `Settings`."""

from __future__ import annotations

import pytest

from panakoes_summarization.config import Settings


@pytest.mark.unit
def test_settings_loads_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings()` exposes the documented defaults when env vars are absent."""
    for var in (
        "JWT_SECRET",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "S3_TRANSCRIPTS_BUCKET",
        "S3_SUMMARIES_BUCKET",
        "DDB_SUMMARIES_TABLE",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    settings = Settings()
    assert settings.service_name == "summarization"
    assert settings.aws_region == "us-east-1"
    assert settings.jwt_issuer == "panakoes-auth"
    assert settings.jwt_audience == "panakoes"
    assert settings.s3_transcripts_bucket == "panakoes-dev-transcripts"
    assert settings.s3_summaries_bucket == "panakoes-dev-summaries"
    assert settings.ddb_summaries_table == "panakoes-dev-summaries"
    assert settings.jwt_secret == ""
    assert settings.anthropic_api_key == ""


@pytest.mark.unit
def test_settings_reads_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override every overridable field."""
    monkeypatch.setenv("JWT_SECRET", "my-secret")
    monkeypatch.setenv("JWT_ISSUER", "alt-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "alt-audience")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abc123")
    monkeypatch.setenv("S3_TRANSCRIPTS_BUCKET", "tx-bucket")
    monkeypatch.setenv("S3_SUMMARIES_BUCKET", "sm-bucket")
    monkeypatch.setenv("DDB_SUMMARIES_TABLE", "sm-table")
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    settings = Settings()
    assert settings.jwt_secret == "my-secret"
    assert settings.jwt_issuer == "alt-issuer"
    assert settings.jwt_audience == "alt-audience"
    assert settings.anthropic_api_key == "abc123"
    assert settings.s3_transcripts_bucket == "tx-bucket"
    assert settings.s3_summaries_bucket == "sm-bucket"
    assert settings.ddb_summaries_table == "sm-table"
    assert settings.aws_region == "us-west-2"
