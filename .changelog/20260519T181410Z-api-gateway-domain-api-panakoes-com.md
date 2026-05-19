---
category: Added
---

- `infra/dev/api-gateway-domain`: live at `https://api.panakoes.com`, mapped to the existing HTTP API stage. Uses the multi-SAN ACM cert from PR #426 (no second cert issuance / renewal lifecycle). Custom domain name flipped from the env-prefixed `api.dev.panakoes.com` placeholder to the same `api.panakoes.com` that the SPA's `.env.example` already documents; production reuses the hostname under a separate AWS account.
