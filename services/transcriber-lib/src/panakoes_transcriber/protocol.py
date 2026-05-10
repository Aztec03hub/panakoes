"""The ``Transcriber`` Protocol every concrete backend implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from panakoes_transcriber.types import TranscriptionResult


@runtime_checkable
class Transcriber(Protocol):
    """Pluggable transcription backend contract.

    Implementations transcribe an in-memory audio blob and return a
    structured ``TranscriptionResult``. Errors raise the documented
    exception hierarchy in ``panakoes_transcriber.errors`` so call sites
    can route on type without inspecting backend-specific exceptions.

    The interface is async because every concrete backend is I/O bound
    (HTTP API, S3 fetch, GPU process subprocess). Synchronous callers
    can run via ``asyncio.run``; high-throughput callers can fan out
    with ``asyncio.gather``.
    """

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe ``audio_bytes`` and return a structured result.

        ``filename`` is the original filename and is used by backends
        that sniff content-type from the extension (e.g. Groq).
        ``language_hint`` is an optional ISO 639-1 code; if ``None`` the
        backend auto-detects.
        """
        ...
