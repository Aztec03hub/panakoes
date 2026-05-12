# Fixture: triggering case for panakoes/jwt-env-var-prefix-mismatch
# Pretend this file lives at services/transcription/validator.py
# (a validator, NOT the auth service) and incorrectly reaches for
# the signer's env var. The real PR #218 bug, reproduced.
import os

# WRONG: validator should use JWT_VERIFY_KEY, not AUTH_JWT_SIGNING_KEY
SIGNING_KEY = os.environ["AUTH_JWT_SIGNING_KEY"]  # noqa
FALLBACK = os.getenv("AUTH_JWT_ISSUER", "dev-default")
