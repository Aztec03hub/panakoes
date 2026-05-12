---
category: Fixed
---

- `infra/dev/api-gateway`: dropped the explicit `POST /v1/auth/sign-up` and `POST /v1/auth/sign-in` routes so the browser CORS preflight (`OPTIONS`) is handled by the `ANY /v1/auth/{proxy+}` catch-all. Unblocks browser login from the admin SPA, which was failing because HTTP API v2 does not auto-provision an `OPTIONS` sibling for explicit method routes and the preflight was hitting the gateway's default 404 handler.
