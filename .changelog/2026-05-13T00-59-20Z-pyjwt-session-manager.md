---
category: Security
---

Migrate `services/session-manager` from `python-jose` to `PyJWT` (PR H of 9 in the ecdsa-CVE remediation series). Drops the transitive `ecdsa` dependency and its minerva timing-attack exposure. JWT verification contract (HS256 + issuer + audience) preserved; all 84 tests pass; `auth.py` retains 100% coverage.
