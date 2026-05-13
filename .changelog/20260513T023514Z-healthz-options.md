---
category: Fixed
---

- `services/health-aggregator`: add OPTIONS handler for /healthz so browser CORS preflight succeeds (APIGW ANY routes forward OPTIONS to the backend).
