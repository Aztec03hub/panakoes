---
category: Added
---

- `infra/dev/api-gateway`: CORS `AllowOrigins` now includes `https://admin.panakoes.com` alongside the existing CloudFront hostname. Required so the deployed admin SPA's API calls don't get blocked by the browser's CORS preflight after PR #426 wired the new domain.
