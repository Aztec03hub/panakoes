"""Environment-variable construction of `JwtValidator`.

Production services call `from_env()` once at startup. Reading the
three settings here (rather than in `JwtValidator.__init__`) keeps
the validator pure: tests construct it with explicit arguments, and
the env-var path lives in one place that a deployment guide can
point at.
"""

from __future__ import annotations

import os

from panakoes_auth_client.errors import JwtConfigError
from panakoes_auth_client.validator import JwtValidator

_REQUIRED_VARS = ("JWT_SECRET", "JWT_ISSUER", "JWT_AUDIENCE")


def from_env() -> JwtValidator:
    """Construct a `JwtValidator` from environment variables.

    Reads `JWT_SECRET`, `JWT_ISSUER`, and `JWT_AUDIENCE`. Raises
    `JwtConfigError` if any is missing or empty. Empty-string is
    treated as missing because it usually indicates a misconfigured
    deployment (e.g. an unresolved Secrets Manager reference).
    """
    missing: list[str] = []
    values: dict[str, str] = {}
    for name in _REQUIRED_VARS:
        raw = os.environ.get(name, "")
        if not raw:
            missing.append(name)
        else:
            values[name] = raw

    if missing:
        joined = ", ".join(missing)
        raise JwtConfigError(f"missing required environment variables: {joined}")

    return JwtValidator(
        secret=values["JWT_SECRET"],
        issuer=values["JWT_ISSUER"],
        audience=values["JWT_AUDIENCE"],
    )
