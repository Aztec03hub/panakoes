---
category: Added
---

- `infra/ami/whisper-stream`: new Packer template bakes the streaming-transcription GPU AMI. Ships faster-whisper-large-v2 CTranslate2 weights at `/opt/whisper/models/large-v2-ct2/` and a 1-second warmup clip at `/opt/whisper/warmup-1s.wav`, matching the transcriber-stream container's startup assertion (design v7 HIGH-03 + NIT-03).
