---
category: Fixed
---

- `scripts/localstack-init.sh`: changed Stripe placeholder value to avoid triggering Trivy's stripe-secret-token pattern on every CI run (false positive; value was `sk_test_placeholder_for_localstack` which matched the `sk_test_` prefix pattern).
