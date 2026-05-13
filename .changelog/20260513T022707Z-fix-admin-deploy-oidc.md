---
category: Fixed
---

- `admin-deploy`: remove `AWS_PROFILE: gha-oidc` job-level env var that caused "Could not load credentials from any providers" after successful OIDC role assumption; update `scripts/deploy-admin-spa.sh` pre-flight to accept ambient OIDC credentials (`AWS_ACCESS_KEY_ID`) as an alternative to a named profile.
