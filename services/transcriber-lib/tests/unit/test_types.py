"""Tests for the shared transcription types and protocol."""

from __future__ import annotations

import pytest

from panakoes_transcriber import (
    Transcriber,
    TranscriberAuthError,
    TranscriberError,
    TranscriberRateLimitError,
    TranscriberRequestError,
    TranscriberTimeoutError,
    TranscriberUpstreamError,
    TranscriptionResult,
    TranscriptionSegment,
    Word,
)

pytestmark = pytest.mark.unit


def test_word_is_frozen_dataclass() -> None:
    word = Word(text="hello", start=0.0, end=0.5)
    assert word.text == "hello"
    assert word.start == 0.0
    assert word.end == 0.5
    with pytest.raises(AttributeError):
        word.text = "world"  # type: ignore[misc]


def test_segment_holds_words_tuple() -> None:
    words = (Word(text="hello", start=0.0, end=0.5),)
    seg = TranscriptionSegment(text="hello", start=0.0, end=0.5, words=words)
    assert seg.words == words


def test_segment_allows_empty_words() -> None:
    seg = TranscriptionSegment(text="hi", start=0.0, end=0.2, words=())
    assert seg.words == ()


def test_result_holds_segments_tuple() -> None:
    seg = TranscriptionSegment(text="hello", start=0.0, end=0.5, words=())
    result = TranscriptionResult(
        text="hello",
        segments=(seg,),
        language="en",
        duration_seconds=0.5,
    )
    assert result.segments == (seg,)
    assert result.language == "en"
    assert result.duration_seconds == 0.5


def test_result_allows_none_language_and_duration() -> None:
    result = TranscriptionResult(text="x", segments=(), language=None, duration_seconds=None)
    assert result.language is None
    assert result.duration_seconds is None


class _FakeTranscriber:
    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        _ = (audio_bytes, filename, language_hint)
        return TranscriptionResult(text="", segments=(), language=None, duration_seconds=None)


def test_protocol_runtime_check_accepts_conforming_class() -> None:
    assert isinstance(_FakeTranscriber(), Transcriber)


def test_protocol_runtime_check_rejects_non_conforming() -> None:
    class NotATranscriber:
        pass

    assert not isinstance(NotATranscriber(), Transcriber)


def test_error_hierarchy() -> None:
    for exc_cls in (
        TranscriberAuthError,
        TranscriberRateLimitError,
        TranscriberRequestError,
        TranscriberTimeoutError,
        TranscriberUpstreamError,
    ):
        assert issubclass(exc_cls, TranscriberError)


def test_rate_limit_error_carries_retry_after() -> None:
    err = TranscriberRateLimitError("slow down", retry_after_seconds=12.5)
    assert err.retry_after_seconds == 12.5
    assert "slow down" in str(err)


def test_rate_limit_error_default_retry_after_is_none() -> None:
    err = TranscriberRateLimitError("nope")
    assert err.retry_after_seconds is None
