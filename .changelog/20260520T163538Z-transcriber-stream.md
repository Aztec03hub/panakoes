---
category: Added
---

- `services/transcriber-stream`: new GPU-side streaming-transcription container (faster-whisper-large + vendored LocalAgreement-2 from QuentinFuxa/WhisperLiveKit). Polls a per-session SQS queue, emits partials over the API Gateway WebSocket management API, drains cleanly on Spot interruption + lifecycle disconnect, fails fast on missing AMI-baked weights. Includes vendor NOTICE drift test.
