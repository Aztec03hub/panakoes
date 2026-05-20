---
category: Added
---

- `docs/design/realtime-streaming-transcription.md`: locked design for the streaming-transcription data plane (browser AudioWorklet to API GW WebSocket to per-session SQS to faster-whisper-large on per-session g4dn.xlarge Spot GPU, partials emitted back via API GW management API). Cleared the five-stage review cycle (architect + four adversarial rounds; trend 5 then 3 then 2 then 2 then 0 BLOCKs). Vendor lift of LocalAgreement-2 + Silero VAD from QuentinFuxa/WhisperLiveKit under Apache-2.0 with NOTICE attribution. Stage 2 dispatches three parallel implementing agents next.
