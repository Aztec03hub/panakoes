---
category: Fixed
---

- `services/billing`: drop the `/billing` router prefix so the API Gateway proxy-strip contract works; `POST /v1/billing/portal-session` now reaches the backend instead of 404ing. Unblocks the "Manage subscription" button on the admin Account page.
- `services/auth`, `services/admin`: rename the whoami route from `/auth/me` to `/me` so the gateway-strip lands on a single segment (`/v1/auth/me`) instead of the doubled `/v1/auth/auth/me`. SPA updated to match.
