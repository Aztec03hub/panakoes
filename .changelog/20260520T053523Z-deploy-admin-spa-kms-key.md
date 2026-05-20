---
category: Fixed
---

- `scripts/deploy-admin-spa.sh`: pin uploads to the `panakoes-dev-frontend` KMS CMK that CloudFront's OAC is scoped to. The bucket's default encryption was changed to the consolidated `panakoes/app-data` CMK by the W2-T1 KMS consolidation, but the OAC role only has decrypt against the legacy frontend key, so every clean deploy via this script was silently 403'ing every path on admin.panakoes.com. The `--sse aws:kms --sse-kms-key-id alias/panakoes-dev-frontend` flags on both `s3 sync` passes force per-object encryption with the right key.
