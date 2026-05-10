"""Groq Whisper-large-v3 transcription backend.

Wraps Groq's hosted ``POST /audio/transcriptions`` endpoint (OpenAI-API
compatible). Configured via constructor; the API key is expected to be
sourced from the ``GROQ_API_KEY`` env var by the caller (the backend
itself is env-agnostic so it composes cleanly with config systems).

Why Groq for the first concrete backend: free dev tier, 5-10x realtime
on Groq's LPU silicon, OpenAI-compatible request/response shape, and
word-level timestamps via ``timestamp_granularities=["word", "segment"]``
with ``response_format=verbose_json``. The "no GPU required" property
unblocks end-to-end demos before our self-hosted GPU vCPU quota lands.
"""

from __future__ import annotations

import io
from typing import Any

import httpx
from panakoes_transcriber import (
    TranscriberAuthError,
    TranscriberRateLimitError,
    TranscriberRequestError,
    TranscriberTimeoutError,
    TranscriberUpstreamError,
    TranscriptionResult,
    TranscriptionSegment,
    Word,
)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "whisper-large-v3"
DEFAULT_TIMEOUT_SECONDS = 60.0


class GroqTranscriberBackend:
    """Concrete ``Transcriber`` backend backed by Groq's hosted Whisper.

    Conforms to the ``panakoes_transcriber.Transcriber`` Protocol; a
    runtime ``isinstance(backend, Transcriber)`` check passes.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Configure the backend with credentials and endpoint.

        ``api_key`` is required; pull it from ``GROQ_API_KEY`` (env var)
        in development or from AWS Secrets Manager
        (``panakoes-dev/groq-api-key``) in deployed environments.
        ``base_url`` is overridable for tests and for any future
        OpenAI-compatible clone hosting. ``transport`` is an injection
        seam for tests; production callers leave it ``None`` to get the
        default ``httpx`` transport.
        """
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe ``audio_bytes`` via Groq and return a structured result.

        Raises one of ``TranscriberAuthError`` (401),
        ``TranscriberRateLimitError`` (429, with parsed ``Retry-After``),
        ``TranscriberRequestError`` (other 4xx),
        ``TranscriberUpstreamError`` (5xx),
        ``TranscriberTimeoutError`` (network timeout).
        """
        url = f"{self._base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        # httpx 0.28 expects file-like objects (not raw bytes) inside ``files``
        # when running under AsyncClient; raw bytes route to a sync stream and
        # raise "Attempted to send a sync request with an AsyncClient
        # instance." Wrap in ``io.BytesIO`` to keep the upload async-safe.
        files = {"file": (filename, io.BytesIO(audio_bytes))}
        # httpx 0.28 only supports the dict-of-values form for ``data`` under
        # AsyncClient; passing a list of (name, value) tuples falls into the
        # same sync-stream path that breaks raw-bytes uploads. We model the
        # repeated-key field (``timestamp_granularities[]``) as a list value
        # under one key, which httpx serializes as two distinct multipart
        # parts. (Verified via inspection of the multipart body in tests.)
        data: dict[str, str | list[str]] = {
            "model": self._model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
        }
        if language_hint is not None:
            data["language"] = language_hint

        client_kwargs: dict[str, Any] = {"timeout": self._timeout_seconds}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(url, headers=headers, files=files, data=data)
        except httpx.TimeoutException as exc:
            raise TranscriberTimeoutError(
                f"Groq request timed out after {self._timeout_seconds}s",
            ) from exc

        _raise_for_status(response)
        payload = response.json()
        return _parse_response(payload)


def _raise_for_status(response: httpx.Response) -> None:
    """Map HTTP error codes onto the Transcriber error hierarchy."""
    status = response.status_code
    if status < 400:
        return
    body_snippet = response.text[:500]
    if status == 401:
        raise TranscriberAuthError(f"Groq rejected credentials: {body_snippet}")
    if status == 429:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise TranscriberRateLimitError(
            f"Groq rate-limited the request: {body_snippet}",
            retry_after_seconds=retry_after,
        )
    if 400 <= status < 500:
        raise TranscriberRequestError(
            f"Groq rejected the request ({status}): {body_snippet}",
        )
    raise TranscriberUpstreamError(
        f"Groq upstream error ({status}): {body_snippet}",
    )


def _parse_retry_after(header_value: str | None) -> float | None:
    """Parse the ``Retry-After`` header as a float of seconds.

    The header may be absent, an integer-string of seconds, or an HTTP
    date. We support the seconds form (the only form Groq sends today)
    and treat anything unparseable as ``None`` so the caller still gets
    the error type without a 500 from us.
    """
    if header_value is None:
        return None
    try:
        return float(header_value)
    except ValueError:
        return None


def _parse_response(payload: dict[str, Any]) -> TranscriptionResult:
    """Map Groq's ``verbose_json`` payload onto the shared result type.

    Groq's response shape for ``verbose_json`` with both word and segment
    granularities is: top-level ``text``, ``language``, ``duration``,
    ``segments`` (each a dict with at least ``id``, ``start``, ``end``,
    ``text``), and a top-level ``words`` array (each ``{word, start, end}``).
    Words are NOT nested inside their segment, so we map words into
    segments by interval-overlap.
    """
    text = str(payload.get("text", ""))
    language_value = payload.get("language")
    language = str(language_value) if language_value is not None else None
    duration_value = payload.get("duration")
    duration = float(duration_value) if duration_value is not None else None

    raw_segments = payload.get("segments") or []
    raw_words = payload.get("words") or []

    words: list[Word] = []
    for raw in raw_words:
        word_text = raw.get("word")
        start = raw.get("start")
        end = raw.get("end")
        if word_text is None or start is None or end is None:
            continue
        words.append(Word(text=str(word_text), start=float(start), end=float(end)))

    segments: list[TranscriptionSegment] = []
    for raw in raw_segments:
        seg_text = str(raw.get("text", ""))
        seg_start = float(raw.get("start", 0.0))
        seg_end = float(raw.get("end", 0.0))
        seg_words = tuple(_words_inside(words, seg_start, seg_end))
        segments.append(
            TranscriptionSegment(
                text=seg_text,
                start=seg_start,
                end=seg_end,
                words=seg_words,
            )
        )

    return TranscriptionResult(
        text=text,
        segments=tuple(segments),
        language=language,
        duration_seconds=duration,
    )


def _words_inside(
    words: list[Word],
    seg_start: float,
    seg_end: float,
) -> list[Word]:
    """Return words whose midpoint lies inside the segment interval.

    Midpoint matching avoids double-attributing a word that straddles a
    segment boundary, while still attributing every word to exactly one
    segment when the segment intervals tile the audio.
    """
    matches: list[Word] = []
    for word in words:
        midpoint = (word.start + word.end) / 2.0
        if seg_start <= midpoint < seg_end:
            matches.append(word)
    return matches
