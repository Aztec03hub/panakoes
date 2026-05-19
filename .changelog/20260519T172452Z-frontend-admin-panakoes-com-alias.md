---
category: Added
---

- `infra/dev/frontend`: CloudFront distribution now serves `https://admin.panakoes.com` (the SvelteKit admin SPA). Backed by a new us-east-1 ACM cert (multi-SAN: admin/api/www/apex panakoes.com); apply waits for cert ISSUED then attaches alias + cert. DNS wiring at Cloudflare (CNAME `admin -> dmaopcm3hnxog.cloudfront.net`, proxy off) is the final step to make the URL reachable.
