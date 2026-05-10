"""Tests for the Groq Transcriber backend.

Mocks the upstream Groq endpoint via ``httpx.MockTransport`` injected
through the backend's ``transport`` test seam. No real network calls.

Background: respx 0.23.1 + httpx 0.28 has a known interaction bug with
async multipart uploads, so we use httpx's first-party MockTransport
which is documented as the supported async-mock primitive.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from panakoes_transcriber import (
    Transcriber,
    TranscriberAuthError,
    TranscriberRateLimitError,
    TranscriberRequestError,
    TranscriberTimeoutError,
    TranscriberUpstreamError,
)

from panakoes_transcriber_groq import GroqTranscriberBackend
from panakoes_transcriber_groq.backend import DEFAULT_BASE_URL

pytestmark = pytest.mark.unit

ENDPOINT = f"{DEFAULT_BASE_URL}/audio/transcriptions"

HAPPY_PAYLOAD = {
    "text": "hello world this is a test",
    "language": "en",
    "duration": 3.0,
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "hello world"},
        {"id": 1, "start": 1.0, "end": 2.0, "text": "this is"},
        {"id": 2, "start": 2.0, "end": 3.0, "text": "a test"},
    ],
    "words": [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.4, "end": 0.9},
        {"word": "this", "start": 1.0, "end": 1.3},
        {"word": "is", "start": 1.3, "end": 1.6},
        {"word": "a", "start": 2.0, "end": 2.1},
        {"word": "test", "start": 2.1, "end": 2.9},
    ],
}


class _Recorder:
    """Capture every request the backend issues for post-hoc assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []


def _make_backend(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "test-key",
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[GroqTranscriberBackend, _Recorder]:
    rec = _Recorder()

    def wrapper(request: httpx.Request) -> httpx.Response:
        # Read the body now while the request is still in scope.
        rec.requests.append(request)
        rec.bodies.append(request.content)
        return handler(request)

    transport = httpx.MockTransport(wrapper)
    backend = GroqTranscriberBackend(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=5.0,
        transport=transport,
    )
    return backend, rec


def _ok(payload: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


def _status(
    code: int,
    *,
    json: dict[str, object] | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        if json is not None:
            return httpx.Response(code, json=json, headers=headers or {})
        return httpx.Response(code, text=text or "", headers=headers or {})

    return handler


def test_constructor_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        GroqTranscriberBackend(api_key="")


def test_backend_satisfies_protocol() -> None:
    backend, _ = _make_backend(_ok(HAPPY_PAYLOAD))
    assert isinstance(backend, Transcriber)


async def test_happy_path_returns_populated_result() -> None:
    backend, rec = _make_backend(_ok(HAPPY_PAYLOAD))
    result = await backend.transcribe(audio_bytes=b"\x00\x01", filename="audio.wav")
    assert len(rec.requests) == 1
    assert str(rec.requests[0].url) == ENDPOINT
    assert result.text == "hello world this is a test"
    assert result.language == "en"
    assert result.duration_seconds == 3.0
    assert len(result.segments) == 3
    assert sum(len(s.words) for s in result.segments) == 6


async def test_word_to_segment_mapping_three_segments_twelve_words() -> None:
    payload = {
        "text": "a b c d e f g h i j k l",
        "language": "en",
        "duration": 6.0,
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "a b c d"},
            {"id": 1, "start": 2.0, "end": 4.0, "text": "e f g h"},
            {"id": 2, "start": 4.0, "end": 6.0, "text": "i j k l"},
        ],
        "words": [
            {"word": "a", "start": 0.0, "end": 0.4},
            {"word": "b", "start": 0.5, "end": 0.9},
            {"word": "c", "start": 1.0, "end": 1.4},
            {"word": "d", "start": 1.5, "end": 1.9},
            {"word": "e", "start": 2.0, "end": 2.4},
            {"word": "f", "start": 2.5, "end": 2.9},
            {"word": "g", "start": 3.0, "end": 3.4},
            {"word": "h", "start": 3.5, "end": 3.9},
            {"word": "i", "start": 4.0, "end": 4.4},
            {"word": "j", "start": 4.5, "end": 4.9},
            {"word": "k", "start": 5.0, "end": 5.4},
            {"word": "l", "start": 5.5, "end": 5.9},
        ],
    }
    backend, _ = _make_backend(_ok(payload))
    result = await backend.transcribe(audio_bytes=b"x", filename="audio.wav")
    assert [len(s.words) for s in result.segments] == [4, 4, 4]
    assert [w.text for w in result.segments[0].words] == ["a", "b", "c", "d"]
    assert [w.text for w in result.segments[1].words] == ["e", "f", "g", "h"]
    assert [w.text for w in result.segments[2].words] == ["i", "j", "k", "l"]


async def test_language_hint_none_omits_language_field() -> None:
    backend, rec = _make_backend(_ok(HAPPY_PAYLOAD))
    await backend.transcribe(audio_bytes=b"x", filename="a.wav", language_hint=None)
    body = rec.bodies[0].decode("utf-8", errors="ignore")
    assert 'name="language"' not in body


async def test_language_hint_passes_language_field() -> None:
    backend, rec = _make_backend(_ok(HAPPY_PAYLOAD))
    await backend.transcribe(audio_bytes=b"x", filename="a.wav", language_hint="en")
    body = rec.bodies[0].decode("utf-8", errors="ignore")
    assert 'name="language"' in body
    assert "en" in body


async def test_unauthorized_raises_auth_error() -> None:
    backend, _ = _make_backend(_status(401, json={"error": "bad key"}))
    with pytest.raises(TranscriberAuthError):
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")


async def test_rate_limit_raises_with_retry_after() -> None:
    backend, _ = _make_backend(
        _status(429, json={"error": "slow down"}, headers={"Retry-After": "12"}),
    )
    with pytest.raises(TranscriberRateLimitError) as excinfo:
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert excinfo.value.retry_after_seconds == 12.0


async def test_rate_limit_without_retry_after_header() -> None:
    backend, _ = _make_backend(_status(429, json={"error": "slow"}))
    with pytest.raises(TranscriberRateLimitError) as excinfo:
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert excinfo.value.retry_after_seconds is None


async def test_rate_limit_unparseable_retry_after_header_returns_none() -> None:
    backend, _ = _make_backend(
        _status(
            429,
            json={"error": "slow"},
            headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"},
        ),
    )
    with pytest.raises(TranscriberRateLimitError) as excinfo:
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert excinfo.value.retry_after_seconds is None


async def test_other_4xx_raises_request_error() -> None:
    backend, _ = _make_backend(_status(400, json={"error": "unsupported"}))
    with pytest.raises(TranscriberRequestError):
        await backend.transcribe(audio_bytes=b"x", filename="a.weird")


async def test_5xx_raises_upstream_error() -> None:
    backend, _ = _make_backend(_status(503, text="oops"))
    with pytest.raises(TranscriberUpstreamError):
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")


async def test_timeout_raises_timeout_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    backend, _ = _make_backend(handler)
    with pytest.raises(TranscriberTimeoutError):
        await backend.transcribe(audio_bytes=b"x", filename="a.wav")


async def test_response_with_missing_language_and_duration() -> None:
    payload = {
        "text": "hi",
        "segments": [{"id": 0, "start": 0.0, "end": 0.5, "text": "hi"}],
        "words": [],
    }
    backend, _ = _make_backend(_ok(payload))
    result = await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert result.language is None
    assert result.duration_seconds is None
    assert result.segments[0].words == ()


async def test_response_drops_word_entries_missing_required_fields() -> None:
    payload = {
        "text": "hi",
        "language": "en",
        "duration": 1.0,
        "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "hi"}],
        "words": [
            {"word": "hi", "start": 0.0, "end": 0.5},
            {"word": "broken"},
            {"start": 0.6, "end": 0.7},
        ],
    }
    backend, _ = _make_backend(_ok(payload))
    result = await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert [w.text for w in result.segments[0].words] == ["hi"]


async def test_request_includes_bearer_auth_and_model() -> None:
    backend, rec = _make_backend(_ok(HAPPY_PAYLOAD))
    await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    request = rec.requests[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    body = rec.bodies[0].decode("utf-8", errors="ignore")
    assert "whisper-large-v3" in body
    assert "verbose_json" in body
    assert body.count("timestamp_granularities[]") == 2


async def test_custom_base_url_is_honored() -> None:
    backend, rec = _make_backend(_ok(HAPPY_PAYLOAD), base_url="https://example.test/v1/")
    await backend.transcribe(audio_bytes=b"x", filename="a.wav")
    assert str(rec.requests[0].url) == "https://example.test/v1/audio/transcriptions"
