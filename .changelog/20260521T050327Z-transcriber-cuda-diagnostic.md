---
category: Changed
---

- `services/transcriber-stream`: log GPU / CUDA / model-dir state at the top of container startup so the Stage 4 backend-factory hang shows up in CloudWatch instead of silently going nowhere. Includes `torch.cuda.is_available()`, `device_count`, `device_name`, the `torch.version.cuda` build tag, `ctranslate2.get_cuda_device_count()`, and a directory listing of `/opt/whisper/models/large-v2-ct2/`.
