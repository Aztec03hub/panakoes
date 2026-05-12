---
category: Changed
---

- `services/admin`: layout now redirects authenticated non-admin users to a new `/forbidden` page instead of rendering the dashboard shell with 403 errors.
- `scripts/deploy-admin-spa.sh`: bakes the full SPA env contract at build time (`VITE_API_BASE_URL`, `VITE_USE_LIVE_HEALTH_AGGREGATOR`, `VITE_OTEL_EXPORTER_OTLP_ENDPOINT`, `VITE_SERVICE_VERSION`, `VITE_DEPLOYMENT_ENVIRONMENT`) via CLI flags or env, with defaults shown in `--help`.
- `.github/workflows/admin-deploy.yml`: new workflow that auto-deploys the admin SPA to the dev S3 + CloudFront on push to `main` under `services/admin/**` (OIDC via `panakoes-github-actions`).
- `infra/dev/ecs`: set `OTEL_SDK_DISABLED=true` on auth, cost-api, and admin-api task definitions to silence the OTLP-to-localhost-4317 retry log spam until an ADOT sidecar pattern is wired up.
- `services/admin/README.md`: document that `$env/dynamic/public` (`_app/env.js`) is intentionally empty for this SPA; the public env contract is `import.meta.env.VITE_*` baked at build time.
