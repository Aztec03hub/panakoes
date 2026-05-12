"""Emit the cost-api OpenAPI 3.1 schema to a checked-in artifact.

The SPA's TypeScript client codegen reads from a stable file rather
than a live API, so this script regenerates `openapi.json` from the
FastAPI app's introspection (`app.openapi()`) and writes it pretty
printed with sorted keys (so diffs are review friendly). CI re-runs
this and fails the PR on any unstaged drift.

The script does NOT start the service, does NOT need AWS credentials,
and does NOT invoke the lifespan; `FastAPI.openapi()` is pure
introspection over route metadata and pydantic models.

The emitted schema strips the `servers` array because deployed host
URLs vary per environment. Clients pass an explicit base URL.

Run directly:
    uv run python scripts/emit-openapi.py
or via the repo root Makefile:
    make openapi-emit
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow direct invocation without installing the package (uv run script).
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVICE_ROOT / "src"))

# Disable OTel SDK and seed minimal env so module-level Settings() and
# `JwtValidator.from_env()` (imported transitively through routes) do
# not error during script import. The values are never used; we never
# hit a real auth check or real boto3 client because we only ask the
# app for its OpenAPI document.
import os  # noqa: E402

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("JWT_SECRET", "emit-openapi-not-a-real-secret")
os.environ.setdefault("JWT_ISSUER", "https://auth.example")
os.environ.setdefault("JWT_AUDIENCE", "cost-api")

from panakoes_cost_api.main import app  # noqa: E402

OUTPUT_PATH = _SERVICE_ROOT / "openapi.json"


def emit() -> Path:
    """Render `app.openapi()` to the checked-in schema artifact."""
    schema = app.openapi()
    # Server URLs are environment specific (dev API Gateway stage URL
    # vs prod), so strip them. Clients pass an explicit base URL.
    schema.pop("servers", None)
    OUTPUT_PATH.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return OUTPUT_PATH


if __name__ == "__main__":
    written = emit()
    sys.stdout.write(f"wrote {written}\n")
