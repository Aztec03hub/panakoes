"""Unit tests for `panakoes_transcribe_worker.config`."""

from __future__ import annotations

import pytest

from panakoes_transcribe_worker.config import load_settings


@pytest.mark.unit
def test_load_settings_happy_path() -> None:
    settings = load_settings()
    assert settings.ddb_ingestion_table
    assert settings.audio_uploads_bucket
    assert settings.aws_region == "us-east-1"


@pytest.mark.unit
def test_load_settings_requires_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDB_INGESTION_TABLE", raising=False)
    with pytest.raises(RuntimeError, match="DDB_INGESTION_TABLE"):
        load_settings()


@pytest.mark.unit
def test_load_settings_requires_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIO_UPLOADS_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="AUDIO_UPLOADS_BUCKET"):
        load_settings()
