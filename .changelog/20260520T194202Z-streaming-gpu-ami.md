---
category: Changed
---

- `infra/dev/ecs`: pin `streaming_gpu_ami_id` to `ami-02df17bc645da78b5` (the freshly-baked whisper-stream AMI containing faster-whisper-large-v2 CTranslate2 weights at `/opt/whisper/models/large-v2-ct2/` and a 1-second warmup clip at `/opt/whisper/warmup-1s.wav`). Removes the placeholder ECS-Optimized GPU AMI default; the transcriber-stream container's startup assertion now passes.
