---
category: Security
---

Migrated `services/notification` from `python-jose` to `PyJWT[crypto]>=2.10.1` to remediate the python-ecdsa Minerva timing-attack CVE pulled in transitively by `python-jose[cryptography]`. Removed the redundant `ecdsa>=0.19.1` pin. Exception classes mapped per the PR A recipe (`JWTError` -> `InvalidTokenError`, `ExpiredSignatureError` preserved). All 75 tests pass; auth coverage remains 100%. PR F of 9 in the ecdsa-CVE remediation series.
