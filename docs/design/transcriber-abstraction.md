# Transcriber pluggable abstraction (design doc)

> **Status:** Proposed. This is a design doc, not an implementation. No code lands until the design is reviewed and approved.

## Goal

Define a `Transcriber` interface and supporting machinery so the panakoes platform can swap transcription backends without touching call sites. Today's primary backend is **Whisper-large-v3 fp16 on AWS Batch (async)** plus **faster-whisper-large with Silero VAD on a session-spawned g4dn.xlarge (streaming)**. Tomorrow's primary backend may be a different self-hosted model (Distil-Whisper, Parakeet, NVIDIA Canary, OWSM-CTC), an AWS-managed service (Amazon Transcribe), or an AI-vendor API (OpenAI Audio, Deepgram, AssemblyAI).

The platform should be able to:

1. **Swap the default backend** behind an environment variable or a configuration value, with no service-level code changes.
2. **A/B test backends** in production by routing a percentage of traffic to a new backend and comparing accuracy / latency / cost.
3. **Per-tenant override** so a customer with a strict data-residency requirement can pin to a specific backend that meets it.
4. **Per-job override** so a streaming session can use the streaming backend while an async upload uses the async backend in the same service.

## Non-goals

- We are not designing a transcoder or pre-processor abstraction; audio decode is a separate concern handled upstream of the `Transcriber`.
- We are not designing a model-fine-tuning interface; that lives in a different system that produces models the `Transcriber` consumes.
- We are not proposing to rewrite the existing async or streaming services; the abstraction is layered such that they can adopt it incrementally.

## Existing state

The async path lives in a future `services/transcriber-batch` (AWS Batch container running Whisper-large-v3). The streaming path lives in `services/gpu-spawner` + a session-spawned EC2 GPU running faster-whisper-large + Silero VAD streaming over WebSocket. Both are committed today as concrete implementations; neither is exposed behind an abstraction. The abstraction work is layered on top.

## The interface

The `Transcriber` interface is mode-aware: a single transcriber instance declares which modes it supports, and the call site invokes the appropriate method.

### Python (Protocol)

```python
from typing import Protocol, AsyncIterator
from dataclasses import dataclass

@dataclass(frozen=True)
class TranscriptionRequest:
    """Input contract for both batch and streaming."""
    audio_uri: str | None  # s3:// URI for batch; None for streaming
    audio_format: str       # "wav", "mp3", "ogg-opus", "webm-opus", "raw-pcm-16khz"
    language_hint: str | None  # ISO 639-1 code; None means auto-detect
    diarize: bool = False  # whether to include speaker diarization
    word_timestamps: bool = False
    vocabulary_hint: list[str] = ()  # rare-word boost

@dataclass(frozen=True)
class TranscriptionWord:
    text: str
    start_seconds: float
    end_seconds: float
    confidence: float

@dataclass(frozen=True)
class TranscriptionSegment:
    text: str
    start_seconds: float
    end_seconds: float
    speaker: str | None  # None unless diarize=True
    words: tuple[TranscriptionWord, ...]
    confidence: float
    is_final: bool  # streaming: True for finalized segments, False for interim

@dataclass(frozen=True)
class TranscriptionResult:
    """Final result of a batch job, or terminal of a stream."""
    segments: tuple[TranscriptionSegment, ...]
    full_text: str
    language: str  # detected or echoed
    duration_seconds: float
    backend_id: str  # which Transcriber implementation produced this

class Transcriber(Protocol):
    """The pluggable interface."""

    backend_id: str  # stable identifier, e.g. "whisper-large-v3-batch"
    supports_batch: bool
    supports_streaming: bool

    async def transcribe_batch(self, request: TranscriptionRequest) -> TranscriptionResult:
        """Transcribe an audio file from S3. Raises BackendError on failure."""
        ...

    async def transcribe_stream(
        self,
        request: TranscriptionRequest,
        audio_chunks: AsyncIterator[bytes],
    ) -> AsyncIterator[TranscriptionSegment]:
        """Yield segments as audio arrives. Final segment has is_final=True."""
        ...
```

### TypeScript (interface)

The TS side is much smaller because admin and auth services don't need to invoke the transcriber directly; they only consume `TranscriptionResult` from DynamoDB. We export a TypeScript interface that mirrors the Python contract for type-safety on the frontend:

```typescript
// services/admin/src/lib/transcription-types.ts
export interface TranscriptionResult {
  backend_id: string;
  segments: ReadonlyArray<TranscriptionSegment>;
  full_text: string;
  language: string;
  duration_seconds: number;
}

// (other types follow the Python shape)
```

## Backends

### Backend 1: WhisperBatchTranscriber (current async, primary)

- `backend_id = "whisper-large-v3-batch"`
- Wraps the existing AWS Batch + EC2 g4dn.xlarge Spot + Whisper-large-v3 fp16 path.
- `supports_batch=True, supports_streaming=False`.
- Strengths: highest accuracy in the open-source-self-hosted tier, low cost-per-audio-minute via Spot pricing.
- Weaknesses: cold-start latency (Batch job start-up plus model load), single-region.

### Backend 2: FasterWhisperStreamingTranscriber (current streaming, primary)

- `backend_id = "faster-whisper-large-streaming"`
- Wraps the GPU-spawner-managed g4dn.xlarge + faster-whisper-large + Silero VAD streaming path.
- `supports_batch=False, supports_streaming=True`.
- Strengths: sub-second latency once warm, real-time interim segments.
- Weaknesses: GPU instance per session is expensive at scale; cold-start latency on first session.

### Backend 3: TranscribeAwsTranscriber (managed fallback)

- `backend_id = "amazon-transcribe"`
- Wraps Amazon Transcribe's batch and streaming APIs. AWS-managed, region-replicated, supports diarization out of the box, has custom-vocabulary support.
- `supports_batch=True, supports_streaming=True`.
- Strengths: zero infrastructure to manage, consistent latency, AWS-native (single billing surface, IAM-native auth).
- Weaknesses: ~3x cost-per-minute vs. self-hosted Whisper, slightly lower accuracy on technical domains, vendor-locked.
- Use cases: failover when self-hosted backend is down; cost-not-king customer tiers; specific compliance regions where Amazon Transcribe has a regional offering and self-hosted does not.

### Backend 4: OpenAIWhisperApiTranscriber (rapid validation)

- `backend_id = "openai-whisper-api"`
- Wraps OpenAI's `/v1/audio/transcriptions` and Realtime API.
- `supports_batch=True, supports_streaming=True` (Realtime API).
- Strengths: trivial setup; useful for rapid validation when adding new audio features (we can verify our pre-processing is correct end-to-end without setting up self-hosted infrastructure first).
- Weaknesses: data leaves AWS, vendor-locked, opaque rate limits.
- Use cases: development and CI; never production-default.

### Backend 5+: future

The interface is designed for additions to be drop-in. Specifically:

- **Distil-Whisper variants** (3-6x faster than Whisper-large-v3 with 1-2% WER regression): same interface; swap the model load path.
- **NVIDIA Canary / Parakeet**: similar shape, different model family; the interface doesn't care.
- **AssemblyAI / Deepgram**: similar to OpenAI-API backend but different vendor.
- **Multimodal backends** (Whisper + GPT-4o follow-up cleanup): becomes a `CompositeTranscriber` that wraps two underlying transcribers and post-processes; the call site is unaffected.

## Routing strategy

The `Transcriber` instance a service uses is selected by a `TranscriberRouter` that takes context and returns the appropriate backend.

```python
class TranscriberRouter:
    def __init__(self, registry: dict[str, Transcriber], policy: RoutingPolicy):
        self._registry = registry
        self._policy = policy

    def select(self, context: RoutingContext) -> Transcriber:
        backend_id = self._policy.route(context)
        return self._registry[backend_id]
```

`RoutingContext` carries:

- `mode`: "batch" or "streaming"
- `tenant_id`: who owns the audio
- `tier`: "free" | "pro" | "team" | "enterprise"
- `region`: the AWS region the request landed in
- `priority`: "interactive" (latency matters most) | "background" (cost matters most)
- `experiment_id`: optional A/B-test bucket id

`RoutingPolicy` is itself a Protocol so we can swap it. Initial implementations:

- `DefaultPolicy`: pick the configured default backend per mode.
- `TenantOverridePolicy`: check a per-tenant routing map; fall through to default.
- `ExperimentPolicy`: route by experiment-id deterministic hash; useful for A/B tests.
- `CostOptimizedPolicy`: pick the cheapest backend that meets the request's latency requirement.

These compose: in production, the typical chain is `TenantOverride > Experiment > Default`.

## Configuration

The router is constructed at service startup from environment variables:

```bash
PANAKOES_TRANSCRIBER_DEFAULT_BATCH=whisper-large-v3-batch
PANAKOES_TRANSCRIBER_DEFAULT_STREAMING=faster-whisper-large-streaming
PANAKOES_TRANSCRIBER_FALLBACK_BATCH=amazon-transcribe
PANAKOES_TRANSCRIBER_FALLBACK_STREAMING=amazon-transcribe
PANAKOES_TRANSCRIBER_EXPERIMENT_ID=2026Q2-distil-whisper-vs-large-v3
PANAKOES_TRANSCRIBER_EXPERIMENT_BUCKET_PCT=10  # 10% of traffic to experimental backend
PANAKOES_TRANSCRIBER_EXPERIMENT_BACKEND=distil-whisper-batch
```

Per-tenant overrides come from DynamoDB (`panakoes-tenant-config` table, `transcriber_override_<mode>` attribute). The Router reads them at the start of each request; cached with a short TTL so a config change propagates within ~30 seconds.

## Observability

Every backend invocation emits an OpenTelemetry span with:

- `panakoes.transcriber.backend_id`
- `panakoes.transcriber.mode` (batch | streaming)
- `panakoes.transcriber.audio_duration_seconds`
- `panakoes.transcriber.processing_time_seconds`
- `panakoes.transcriber.cost_estimate_usd_cents`
- `panakoes.transcriber.tenant_id`

Plus a counter `panakoes.transcriber.invocations_total` labeled by `(backend_id, mode, outcome)` and a histogram `panakoes.transcriber.audio_duration_to_processing_ratio` per `backend_id` for quick "this backend got slower" detection.

A separate Athena-queryable accuracy log captures `(job_id, backend_id, manual_correction_count)` so we can compute per-backend WER drift over time on production traffic.

## Failure modes

The `Transcriber` interface raises `BackendError` (or its subclasses):

- `BackendError`: generic; backend reported a failure. Caller may retry against a different backend per `RoutingPolicy`.
- `RetryableBackendError`: transient (rate limit, network blip, model still loading). Caller retries against the same backend with backoff.
- `RequestRejectedError`: the input is invalid (unsupported format, file too large, language not supported). Caller surfaces to user; do not retry.
- `BackendUnavailableError`: backend is in maintenance or completely failed health-check. Router routes to fallback backend; alerts pager.

The Router exposes a circuit-breaker so a backend that has thrown `BackendUnavailableError` repeatedly is taken out of rotation for ~5 minutes before being probed again.

## Testing

Each `Transcriber` implementation has:

- **Contract tests** (shared across all backends) verifying the interface contract: empty audio raises `RequestRejectedError`, valid audio returns a non-empty `TranscriptionResult`, unsupported language raises the right error, etc. These use a shared `TestTranscriberContract` mixin.
- **Backend-specific tests** for the implementation details: AWS API mocks (`moto`/`testcontainers`) for the AWS-backed backends, recorded HTTP fixtures (`pytest-recording`) for the vendor-API backends, real audio fixture files for the self-hosted backends.

A `FakeTranscriber` implementation (returns a fixed transcript from a fixed audio fingerprint) backs all unit tests of upstream services that consume the interface, so those tests don't need GPUs or vendor accounts.

## Open questions for review

1. **Streaming chunk size.** Should `audio_chunks` be raw PCM frames (let each backend chunk for its own model) or pre-chunked at a fixed window (simpler call sites)? Recommendation: raw PCM frames; backends differ in optimal window size and we shouldn't push that into the call site.
2. **Word-level vs. segment-level finalization in streaming.** Whisper-family models emit segment-level chunks; some other backends emit word-by-word. The interface declares segments as the unit; word-level becomes a degenerate segment with one word. Acceptable?
3. **Cost estimation.** The `cost_estimate_usd_cents` span attribute requires each backend to know its own per-minute cost. Easy for managed APIs; for self-hosted (Whisper on Spot GPU) the cost is proportional to instance-hours and varies by instance type and Spot price. Recommendation: emit a per-backend cost estimator function that takes `audio_duration_seconds` and the relevant runtime metadata; default to a conservative on-demand rate when Spot price is unknowable.
4. **Diarization API quality.** Different backends' diarization quality varies wildly. Recommendation: surface a `panakoes.transcriber.diarization_method` attribute so consumers can opt in to "only diarize when backend is X."
5. **Multi-channel audio.** Some backends accept stereo and emit per-channel transcripts; others mix to mono first. Should the interface require per-channel output? Recommendation: yes, with a `channel: int` field on each `TranscriptionSegment`. Backends that mix to mono emit `channel=0` only.

## Implementation phases

This design lands in stages:

**Phase 1: define the interface (this PR's follow-up).** Add `panakoes_transcriber/__init__.py` with the Protocol + dataclasses. No backends yet. Pure type-level addition.

**Phase 2: refactor existing backends to the interface.** Wrap the existing batch and streaming paths as `WhisperBatchTranscriber` and `FasterWhisperStreamingTranscriber`. Call sites stay the same; they get a `Transcriber` instance instead of calling AWS Batch / GPU-spawner directly.

**Phase 3: add the router.** Wire `TranscriberRouter` into the call sites that previously instantiated a backend directly.

**Phase 4: add the AWS Transcribe fallback.** Wire `TranscribeAwsTranscriber` as the failover. First production deployment; validates the fallback path against real traffic.

**Phase 5: add per-tenant overrides + experiments.** Wire DynamoDB-backed routing.

**Phase 6+: additional backends.** Distil-Whisper, vendor APIs, etc.

## References

- [`docs/architecture.md`](../architecture.md): the full data flow of which the transcriber is one node.
- [`SCOPE.md`](../../SCOPE.md): MVP scope locks "pluggable transcription" as a v0.1 commitment.
- ADR-021 to ADR-030 for project conventions this design respects (worktrees, audit, observability, CI/CD).
- Whisper paper: Radford et al., 2022.
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Silero VAD: https://github.com/snakers4/silero-vad
