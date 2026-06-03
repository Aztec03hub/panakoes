---
category: Fixed
---

- `services/api-index`: 404 responses now echo the client-requested path instead of leaking the API Gateway stage prefix (e.g. `/dev/foo`).
