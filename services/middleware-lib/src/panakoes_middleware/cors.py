"""Project-default CORS middleware factory.

Starlette ships a CORS middleware that does the right thing once it is
configured carefully. This module wraps it with the values Panakoes
services agree on: the methods the public API exposes, credentialed
requests, and an env-driven origin allowlist.

`make_cors_middleware` returns a `Middleware` instance ready to slot
into `FastAPI(... middleware=[...])`. `from_env` reads the
`CORS_ALLOWED_ORIGINS` env var (comma separated) and hands the parsed
list to `make_cors_middleware`. Splitting them keeps tests honest:
unit tests construct the middleware directly without touching env.
"""

from __future__ import annotations

import os

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

DEFAULT_METHODS: list[str] = ["GET", "POST", "DELETE", "OPTIONS"]
"""HTTP methods Panakoes APIs expose to browser clients by default."""


def make_cors_middleware(
    allowed_origins: list[str],
    allowed_methods: list[str] | None = None,
    allow_credentials: bool = True,
) -> Middleware:
    """Build a Starlette CORS `Middleware` with project defaults.

    Wildcard origins are not used. Browsers refuse to send credentials
    to a `*` origin, and explicit allowlists make audit trails
    actionable. Callers can pass `["*"]` if they really mean it; we do
    not silently rewrite that intent.
    """
    methods = list(allowed_methods) if allowed_methods is not None else list(DEFAULT_METHODS)
    return Middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=methods,
        allow_credentials=allow_credentials,
        allow_headers=["*"],
    )


def from_env(
    env_var: str = "CORS_ALLOWED_ORIGINS",
    allowed_methods: list[str] | None = None,
    allow_credentials: bool = True,
) -> Middleware:
    """Read origins from `env_var` (comma-separated) and build the middleware.

    Empty or unset env values produce an empty allowlist; that is the
    safe default for production where the operator MUST configure
    allowed origins explicitly. Whitespace around each entry is
    trimmed, and empty entries are dropped.
    """
    raw = os.environ.get(env_var, "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return make_cors_middleware(
        allowed_origins=origins,
        allowed_methods=allowed_methods,
        allow_credentials=allow_credentials,
    )
