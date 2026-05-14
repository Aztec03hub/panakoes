---
category: Fixed
---

- `infra/dev/data`: add missing `panakoes-dev-billing-events` DynamoDB table and export ARN/name outputs for both billing tables; resolves `ResourceNotFoundException` on billing service event-history and subscription-lookup routes.
