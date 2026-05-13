---
category: Fixed
---

- `services/admin`: revert Health tab endpoint from /healthz (liveness probe) to /health (authenticated snapshot) so the dashboard shows actual service health data.
