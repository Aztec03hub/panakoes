# Fixture: non-triggering case. A validator correctly reads JWT_*.
import os

VERIFY_KEY = os.environ["JWT_VERIFY_KEY"]
ISSUER = os.getenv("JWT_ISSUER", "panakoes-auth")
