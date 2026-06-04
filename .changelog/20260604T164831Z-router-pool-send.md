---
category: Fixed
---

- `infra/api-gateway-ws`: streaming-router can now actually deliver audio frames; its SendMessage grant only covered the legacy single frames queue, so every frame sent to the pooled per-session queues failed AccessDenied and no live session ever produced a transcript.
