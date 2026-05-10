# panakoes-transcriber

Pluggable Transcriber abstraction for Panakoes services. This lib defines the shared types and the `Transcriber` Protocol that every concrete transcription backend implements. It contains no backend code itself; concrete backends live in their own `services/transcriber-*` packages and depend on this lib for the shared contract.

Per ADR-009 (transcription pluggable abstraction; see `docs/design/transcriber-abstraction.md` for the full design), call sites depend on this Protocol rather than any concrete backend so swapping backends is configuration, not a code change.

## Public API

```python
from panakoes_transcriber import (
    Transcriber,
    TranscriptionResult,
    TranscriptionSegment,
    Word,
    TranscriberError,
    TranscriberAuthError,
    TranscriberRateLimitError,
    TranscriberRequestError,
    TranscriberTimeoutError,
    TranscriberUpstreamError,
)
```

### Types

`Word` (`text`, `start`, `end`), `TranscriptionSegment` (`text`, `start`, `end`, `words`), `TranscriptionResult` (`text`, `segments`, `language`, `duration_seconds`). All frozen dataclasses; all time fields are seconds from the start of the audio.

### Protocol

```python
class Transcriber(Protocol):
    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult: ...
```

`@runtime_checkable`, so `isinstance(backend, Transcriber)` works for duck-typed call-site assertions.

### Errors

Every backend maps its native errors into the `TranscriberError` subclass hierarchy so call sites can route on type without inspecting backend-specific exceptions. `TranscriberRateLimitError` carries an optional `retry_after_seconds` parsed from the upstream's `Retry-After` header when present.

## Backends

This lib ships no backends. Concrete backends:

- `services/transcriber-groq/` (Groq Whisper-large-v3 hosted API; first concrete backend, shipped 2026-05-09).
- `services/transcriber-openai/` (OpenAI Whisper API; planned).
- `services/transcriber-whisper-gpu/` (self-hosted Whisper-on-GPU via AWS Batch; planned, the long-term cost-control path).

## Scope

The v0.1 contract covers `transcribe` of an in-memory audio blob (the async batch path). Streaming, diarization, and multi-channel are deferred to a follow-up interface revision; they live in the design doc but are not in this lib yet.

## References

- `docs/design/transcriber-abstraction.md`: full design including the routing layer.
- `PLANNING.md` ADR-009: locked architectural decision.
