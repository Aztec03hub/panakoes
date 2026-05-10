# ADR-037: Pluggable Transcriber abstraction with three concrete backends

## Status

Accepted. Lived since PR #169 (Groq + abstraction) and PR #178 (OpenAI + ingestion-api wire).

## Context

ADR-009 established that the transcription path is a pluggable abstraction; v0.1 needs at least one working backend behind it. Two forces shaped which backends actually shipped:

1. **AWS GPU vCPU quota was denied / pending appeal** during initial dev. Both the on-demand G/VT instance quota (`L-DB2E81BA`) and the Spot G/VT quota (`L-3819A6DF`) sat in CASE_OPENED state, blocking the bespoke Whisper-on-GPU path that was always intended as the long-run, lowest-marginal-cost option (see ADR-035 for the broader new-account friction story). v0.1 needed transcription to work without GPU access.
2. **Per-tier requirements diverge.** A free dev tier wants the cheapest possible backend even at the cost of a less-mature compliance posture; an enterprise customer wants a vendor with a clear data-retention policy and a multi-year track record; a cost-controlled high-volume deployment wants self-hosted GPU. No single backend covers all three.

The Transcriber abstraction in `services/transcriber-lib/` was already a Protocol shape; the question was how many concrete implementations to ship and maintain.

## Decision

Ship and maintain three concrete backends concurrently. The consumer service selects one via the `TRANSCRIBER_BACKEND` environment variable (default `groq` for dev). All three implement the `Transcriber` Protocol from `services/transcriber-lib/` and produce the same `TranscriptionResult` shape.

### Shared library: `services/transcriber-lib/`

Defines:

- The `Transcriber` Protocol (one async method, `transcribe(audio: bytes, ...) -> TranscriptionResult`).
- Result types: `Word` (single word with start/end timestamps), `TranscriptionSegment` (sentence-level grouping), `TranscriptionResult` (envelope with text, segments, words, language, duration).
- Error taxonomy: `TranscriberAuthError`, `TranscriberRateLimitError` (carrying parsed `Retry-After`), `TranscriberRequestError`, `TranscriberUpstreamError`, `TranscriberTimeoutError`. Backends raise from this taxonomy; consumers catch from this taxonomy.

### The three backends

**`GroqTranscriberBackend`** (`services/transcriber-groq/`, PR #169). Whisper-large-v3 hosted on Groq's LPU silicon. Free dev tier (rate-limited but generous), 5-10x realtime per request, sub-second latency for short audio. OpenAI-compatible API surface, which keeps the wrapper thin. Default for dev.

**`OpenAITranscriberBackend`** (`services/transcriber-openai/`, PR #178). The `whisper-1` endpoint at $0.006/min. Enterprise compliance posture: clearer data retention policy, no API training on customer data by default, multi-year track record that procurement teams already have on file. The right backend for paid-tier customers whose security review will include "where does the audio go."

**`WhisperGPUTranscriberBackend`** (planned, blocked on AWS quota). Self-hosted Whisper-large-v3 fp16 on EC2 g4dn.xlarge Spot via AWS Batch + a custom AMI. Pennies per audio hour at scale. Will land once the GPU vCPU quota appeal succeeds; the abstraction means it can ship without disturbing the other two.

## Consequences

**Positive.**

- Per-tier backend selection is a config flip, not a code change. Free dev users on Groq, paid customers on OpenAI, future high-volume self-hosted on WhisperGPU; all share the same downstream summarization and storage path.
- The architecture absorbs vendor pricing changes. When Groq disabled their paid tier in May 2026, dev kept working unmodified; the alternative was already wired.
- The end-to-end smoke test (PR #178) proved the Groq path works without any GPU access, unblocking v0.1 ship even with the AWS quota appeal still open.
- The quota-blocked `WhisperGPUTranscriberBackend` does not block anything else. It will land as additive surface area.
- The error taxonomy lives in the shared library, so consumer services (`ingestion-api`, `transcribe-worker`) catch a uniform set of exceptions regardless of which backend is active.

**Negative.**

- Three backends to maintain, not one. Bug fixes in the shared retry / error-mapping layer are one place; backend-specific quirks (auth header shape, multipart encoding, response envelope) are three.
- New error-shape additions touch all three backends. A hypothetical `TranscriberQuotaExhaustedError`, for example, would need each backend's HTTP-status mapping updated and a corresponding test. The discipline is mechanical, but it is discipline.
- Each new backend brings its own httpx 0.28 multipart-encoding workaround (the form-data + binary file pattern that httpx 0.28 changed). The workaround is documented in inline comments in `services/transcriber-groq/src/.../backend.py`; future backends MUST replicate the pattern.

### The leading-space gotcha

Per `feedback_whisper_api_leading_space.md`, every Whisper API response's `text` field has a leading space. This is a Whisper convention, not a bug, and it is consistent across Groq, OpenAI, and self-hosted Whisper. Each backend wrapper strips it via `.lstrip()` before constructing the `TranscriptionResult`. New backends MUST do the same; the test suite at the wrapper level asserts no leading space on `result.text`. The cost of forgetting is downstream rendering bugs that are obvious in the UI but invisible in unit tests, so the assertion lives at the contract boundary.

### The integration target

`services/ingestion-api` (PR #178) selects the backend via `TRANSCRIBER_BACKEND` env var and exposes both:

- An on-demand `POST /api/v1/transcribe/{id}` route, for synchronous transcription of an already-uploaded audio object.
- An automatic S3-event-triggered consumer Lambda (`services/transcribe-worker/`, PR #181) that runs transcription on object-create events without a caller-side trigger.

Both paths use the same backend selection and the same `TranscriptionResult` shape downstream.

## Alternatives considered

**Ship one backend (Groq) for v0.1, add others later.** Rejected: the abstraction was already designed (ADR-009), so the marginal cost of the second backend was small. Shipping only Groq would also mean every paid-tier customer review surfaces "third-party hosted on Groq's LPU silicon" with no enterprise alternative on the menu, which is a worse sales posture than necessary.

**Ship only the GPU backend, wait for the quota appeal.** Rejected: indefinite block on v0.1 transcription. The AWS quota appeal had no firm timeline; the right move was to ship CPU/hosted backends first and let the GPU backend land additively.

**Build a single backend that proxies all three through a unified gateway.** Rejected: the gateway is an extra service to deploy, monitor, and pay for, and it adds latency to every transcription call. The Protocol-based abstraction in `services/transcriber-lib/` achieves the same uniformity at the type-system level with zero runtime overhead.

**Use a third-party transcription aggregator (AssemblyAI, Deepgram).** Considered: simpler ops surface (one vendor, one API, one bill). Rejected because the long-run goal is self-hosted GPU at pennies per hour; an aggregator locks the cost floor at the aggregator's per-minute rate, which is exactly the cost we want to escape. Three-backend pluggability lets us route per-tier and migrate volume to GPU as quota lands.

## References

- ADR-009 (transcription pluggable abstraction; the parent decision this ADR makes concrete).
- ADR-035 (new AWS account friction; the broader story behind the GPU quota block).
- PR #169 (Groq backend + `transcriber-lib` Protocol and error taxonomy).
- PR #178 (OpenAI backend + `ingestion-api` wire-through + end-to-end smoke test).
- PR #181 (`transcribe-worker` auto-trigger off S3 events).
- `services/transcriber-lib/` (shared Protocol, types, error taxonomy).
- `services/transcriber-groq/` (Groq backend; httpx 0.28 multipart workaround comments).
- `services/transcriber-openai/` (OpenAI backend).
- `services/ingestion-api/` (consumer; `TRANSCRIBER_BACKEND` env var selection).
- `services/transcribe-worker/` (S3-event-triggered consumer Lambda).
- `feedback_whisper_api_leading_space.md` (the leading-space convention every backend wrapper handles).
- `feedback_boto3_s3_kms_sigv4.md` (S3+KMS sigv4 quirk relevant to the worker path).
