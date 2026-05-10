"""Shared transcription result dataclasses.

These types are intentionally backend-agnostic and live in this lib so
every concrete backend (Groq, OpenAI Whisper API, self-hosted Whisper on
GPU, AWS Transcribe, ...) returns the same shape to call sites.

The shape is a deliberate simplification of the richer design in
``docs/design/transcriber-abstraction.md``: it covers the v0.1 contract
(``transcribe`` of an in-memory audio blob) and grows by additive
fields. Streaming + diarization + multi-channel are deferred to a
follow-up interface revision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    """A single word with its time interval inside the audio.

    ``start`` and ``end`` are seconds from the start of the audio. They
    are floats because backends emit fractional-second precision.
    """

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class TranscriptionSegment:
    """A segment of transcribed audio.

    A segment is the natural sub-unit a Whisper-family model emits:
    typically a sentence or short utterance. ``words`` may be empty if
    the backend does not support word-level timestamps; callers must
    handle that case.
    """

    text: str
    start: float
    end: float
    words: tuple[Word, ...]


@dataclass(frozen=True)
class TranscriptionResult:
    """The full result of a transcription call.

    ``language`` is the detected (or echoed) ISO 639-1 language code or
    ``None`` if the backend did not report one. ``duration_seconds`` is
    the total audio duration as reported by the backend; ``None`` if the
    backend does not report it.
    """

    text: str
    segments: tuple[TranscriptionSegment, ...]
    language: str | None
    duration_seconds: float | None
