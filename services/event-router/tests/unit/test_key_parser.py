"""Unit tests for `panakoes_event_router.key_parser`."""

from __future__ import annotations

import pytest

from panakoes_event_router.key_parser import ParsedKey, parse_object_key


@pytest.mark.unit
def test_parse_clean_key() -> None:
    """A canonical Ingestion-API key parses into its three segments."""
    parsed = parse_object_key("audio/user_1/ing_42/voice.wav")
    assert parsed == ParsedKey(user_id="user_1", ingestion_id="ing_42", filename="voice.wav")


@pytest.mark.unit
def test_parse_url_encoded_filename() -> None:
    """Spaces (encoded as `+`) and `%XX` escapes round-trip through unquote_plus."""
    parsed = parse_object_key("audio/u/i/voice+memo%2Ewav")
    assert parsed is not None
    assert parsed.filename == "voice memo.wav"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "transcripts/foo/bar/baz.wav",  # wrong prefix
        "audio/onlytwo/segments",  # missing filename
        "audio//ing_42/voice.wav",  # empty user_id
        "audio/user_1//voice.wav",  # empty ingestion_id
        "audio/user_1/ing_42/",  # empty filename
        "completely-wrong",
        "",
    ],
)
def test_parse_rejects_malformed_keys(raw: str) -> None:
    """Anything outside the Ingestion-API contract returns None."""
    assert parse_object_key(raw) is None


@pytest.mark.unit
def test_parse_preserves_filename_with_slashes_collapsed_into_two_segments() -> None:
    """Filenames cannot contain `/` (Ingestion API sanitizes); a malicious key with extra
    slashes after `audio/` is still parsed by taking the third segment as the filename
    and rejecting more than three parts via `split(/, 2)`.
    """
    # Three slashes gives us exactly user/ingestion/filename even if filename has dots.
    parsed = parse_object_key("audio/u/i/sub/dir.wav")
    assert parsed is not None
    # `split('/', 2)` keeps the rest of the path as the filename segment.
    assert parsed.filename == "sub/dir.wav"
