---
category: Security
---

Migrate `services/ws-authorizer` from `python-jose` to `PyJWT[crypto]>=2.10.1` (PR I of 9 in the ecdsa-CVE remediation series). Replaces the `jose as jose_jwt` import alias with `jwt as pyjwt` in `authorizer.py`, swaps `jose_jwt.get_unverified_claims(token)` for `pyjwt.decode(token, options={"verify_signature": False})`, and updates the Dockerfile pip install + conftest test helper to use PyJWT. 100% coverage on the WebSocket auth gate preserved.
