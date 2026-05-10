# services/transcriber-groq

Groq Whisper-large-v3 hosted-API backend implementing the Panakoes `Transcriber` Protocol. This is the first concrete `Transcriber` backend (per ADR-009; see `docs/design/transcriber-abstraction.md`).

## Why Groq

- Free dev tier; no AWS GPU vCPU quota required to demo end-to-end audio-to-transcript.
- 5-10x realtime on Groq's LPU silicon for Whisper-large-v3.
- OpenAI-compatible request/response shape, so the implementation port is small.
- Word-level timestamps available via `timestamp_granularities=["word", "segment"]` with `response_format=verbose_json`.

Self-hosted Whisper-on-GPU (the long-term cost-control path) and OpenAI Whisper API (the enterprise compliance path) are planned follow-up backends; they slot into the same Protocol with no call-site changes.

## Public API

```python
from panakoes_transcriber_groq import GroqTranscriberBackend

backend = GroqTranscriberBackend(
    api_key=os.environ["GROQ_API_KEY"],
    model="whisper-large-v3",                   # default
    base_url="https://api.groq.com/openai/v1",  # default
    timeout_seconds=60.0,                       # default
)
result = await backend.transcribe(
    audio_bytes=blob,
    filename="meeting.wav",
    language_hint="en",  # optional ISO 639-1; omit for auto-detect
)
```

`result` is a `panakoes_transcriber.TranscriptionResult` with `text`, `segments` (each carrying its own `words` tuple by interval-overlap mapping), `language`, and `duration_seconds`.

## Configuration

The backend itself is env-agnostic; it accepts `api_key` as a constructor argument. The convention for the rest of Panakoes is to pull from `GROQ_API_KEY` (env var) in development and from AWS Secrets Manager (`panakoes-dev/groq-api-key`) in deployed environments. The Secrets Manager entry is NOT yet provisioned; that is operator follow-up Terraform work and lands in a separate PR before any deployed environment uses this backend.

## Errors

Mapped onto the shared error hierarchy in `panakoes_transcriber.errors`:

| HTTP | Exception | Notes |
|---|---|---|
| 401 | `TranscriberAuthError` | Bad / missing / revoked key |
| 429 | `TranscriberRateLimitError` | Carries `retry_after_seconds` parsed from `Retry-After` |
| other 4xx | `TranscriberRequestError` | Caller surfaces; do not retry |
| 5xx | `TranscriberUpstreamError` | Caller may retry with backoff |
| timeout | `TranscriberTimeoutError` | After the configured `timeout_seconds` |

## Testing

`tests/unit/test_groq_backend.py` mocks the Groq HTTP endpoint via `httpx.MockTransport` (httpx's first-party async-safe mock primitive; respx 0.23 + httpx 0.28 has a known interaction bug with async multipart uploads). The backend exposes a `transport=` constructor kwarg so tests can inject the mock without touching the network. Coverage gate is 80% (set in `pyproject.toml`).

## References

- `services/transcriber-lib/`: the shared Protocol and types this backend implements.
- `docs/design/transcriber-abstraction.md`: full design doc.
- `PLANNING.md` ADR-009: the locked architectural decision.
- Groq audio docs: <https://console.groq.com/docs/speech-text>.
