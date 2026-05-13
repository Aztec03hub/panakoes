---
category: Security
---

Migrate `services/ingestion-api` from `python-jose` to `PyJWT[crypto]>=2.10.1` to remediate the python-ecdsa CVE chain (PR E of 9). Replaces `jose.JWTError` with `jwt.InvalidTokenError` and updates mypy overrides accordingly; PyJWT honors the existing `audience=`, `issuer=`, and `options` kwargs, so the verification contract is preserved. All 76 tests pass; `auth.py` remains at 100% coverage.
