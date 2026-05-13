### Security
- Migrated `services/gpu-spawner` from `python-jose` to `PyJWT[crypto]>=2.10.1` to remediate the transitive `ecdsa` CVE (PR D of 9). `jose.JWTError` mapped to `jwt.InvalidTokenError`; `jose.ExpiredSignatureError` mapped to `jwt.ExpiredSignatureError`. All 51 tests pass; `auth.py` coverage holds at 100%.
