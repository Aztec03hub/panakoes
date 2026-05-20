---
category: Added
---

- `services/transcribe-worker` + `services/transcriber-batch` + `infra/dev/batch` + `infra/dev/transcribe-worker`: real Whisper-on-Batch async transcription. When `TRANSCRIBER_BACKEND=batch` (now the default), the transcribe-worker Lambda submits an AWS Batch job that runs Whisper-large-v3 fp16 on a g4dn.xlarge Spot GPU via the panakoes-dev-transcriber-batch container. The container is self-contained (openai-whisper + torch+cu124 bundled, weights downloaded on first run), updates the ingestion DDB row directly on completion, and matches the architectural intent documented in CLAUDE.md + ADR-037 + README. The legacy Groq + OpenAI synchronous paths remain working via the same backend-selector env var.
