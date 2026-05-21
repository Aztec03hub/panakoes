---
category: Changed
---

- `services/gpu-spawner`: transcriber-stream container now logs via the awslogs driver to `/panakoes/dev/transcriber-stream` (one stream per session id). Replaces the previous `journald` driver which made container logs unreachable without SSH access to the GPU EC2.
