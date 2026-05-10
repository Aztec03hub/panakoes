"""Unit tests for `panakoes_transcribe_worker.key_parser`."""

from __future__ import annotations

import pytest

from panakoes_transcribe_worker.key_parser import parse_object_key


@pytest.mark.unit
def test_parses_canonical_layout() -> None:
    parsed = parse_object_key("audio/u1/i1/voice.wav")
    assert parsed is not None
    assert parsed.user_id == "u1"
    assert parsed.ingestion_id == "i1"
    assert parsed.filename == "voice.wav"


@pytest.mark.unit
def test_decodes_url_encoded_filename() -> None:
    parsed = parse_object_key("audio/u1/i1/voice+memo.wav")
    assert parsed is not None
    assert parsed.filename == "voice memo.wav"


@pytest.mark.unit
def test_filename_with_slashes_collapses_to_three_segments() -> None:
    # split("/", 2) yields exactly three; everything after the second
    # slash is treated as the filename.
    parsed = parse_object_key("audio/u1/i1/sub/dir/voice.wav")
    assert parsed is not None
    assert parsed.filename == "sub/dir/voice.wav"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "video/u1/i1/voice.wav",  # wrong prefix
        "audio/u1/i1",  # too few segments
        "audio//i1/voice.wav",  # empty user_id
        "audio/u1//voice.wav",  # empty ingestion_id
        "audio/u1/i1/",  # empty filename
        "",
        "random",
    ],
)
def test_returns_none_on_malformed(raw: str) -> None:
    assert parse_object_key(raw) is None
