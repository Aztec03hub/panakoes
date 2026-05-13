---
category: Security
---

- `services/test-helpers`: migrate JWT helper from `python-jose` to `PyJWT[crypto]>=2.10.1` to drop the unpatched python-ecdsa Minerva CVE (GHSA-wj6h-64fc-37mp). Public API (`make_test_token`, `make_expired_token`, `bearer_header`) is unchanged; HS256 contract preserved.
