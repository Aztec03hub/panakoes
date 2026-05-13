---
category: Fixed
---

- `services/admin`: Health tab now loads live data by calling the correct public liveness endpoint (/healthz) instead of the auth-gated /health snapshot.
