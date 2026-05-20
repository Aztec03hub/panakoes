---
category: Added
---

- `services/admin`: new `/upload` route in the admin SPA. Lets a logged-in user pick an audio file, request a pre-signed S3 PUT from `ingestion-api`, upload the bytes, and redirect to `/ingestion/[id]` which polls the transcript + AI summary.
- `services/admin`: new `/ingestion/[id]` route polls `query-api` for transcript status + the AI-generated summary, with a status badge and exponential-backoff polling. Closes the loop on the "upload audio, see what came back" demo path.
- `services/admin/lib/api.ts`: new `createIngestion`, `uploadToPresigned`, `fetchIngestion`, `fetchSummary` helpers backing the new routes.
- `infra/dev/ecs/ingestion_api.tf`: renamed the JWT validator env vars from `AUTH_JWT_SECRET` / `AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE` to `JWT_SECRET` / `JWT_ISSUER` / `JWT_AUDIENCE` to match what `ingestion-api`'s pydantic-settings actually reads. The old names silently fell back to a dev-only secret and rejected every real JWT with 401 invalid_token. Same failure mode PR #218 fixed for cost-api / admin-api.
