---
category: Fixed
---

- `services/auth`: add Hono `cors()` middleware to short-circuit OPTIONS preflight at the backend. The API Gateway `ANY /v1/auth/{proxy+}` catch-all forwards OPTIONS to the backend, which previously 404'd. Browser-driven login was therefore impossible. Middleware responds 204 with Access-Control-* headers before any route runs.
