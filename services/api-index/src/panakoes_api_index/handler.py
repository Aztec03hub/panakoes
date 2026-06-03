"""Lambda handler for the Panakoes api-index service.

Backs three behaviors on the public HTTP API (API Gateway v2):

  - `GET /`     : content-negotiated root index. Browsers (Accept
                  contains `text/html`) get a polished self-contained
                  HTML landing page; everything else gets a JSON index.
  - `GET /health`: a fast liveness probe for the index service itself.
                  It does NOT fan out to backend services. A root index
                  must stay cheap and always-up; coupling its health to
                  every backend would make the front door flap whenever
                  any one service blips, which is exactly backwards for a
                  page Phil shows in interviews. Aggregate health lives
                  behind `/v1/health-aggregator/`.
  - anything else (`$default`): a friendly 404 (JSON, or small HTML for
                  browsers) that points the caller at `GET /`.

The handler is pure: it reads the incoming API Gateway v2 (payload
format 2.0) event and returns a response dict. No AWS calls, no I/O, so
it is trivially unit-testable and has a sub-millisecond warm path.
"""

from __future__ import annotations

import json
from typing import Any

from . import catalog, render

_JSON_CT = "application/json"
_HTML_CT = "text/html; charset=utf-8"


def _wants_html(event: dict[str, Any]) -> bool:
    """Return True if the client prefers HTML.

    API Gateway v2 lowercases header keys in payload format 2.0, but we
    look the header up case-insensitively to stay robust against format
    1.0 events and direct invocations in tests.
    """
    headers = event.get("headers") or {}
    accept = ""
    for key, value in headers.items():
        if key.lower() == "accept":
            accept = value or ""
            break
    return "text/html" in accept.lower()


def _request_path(event: dict[str, Any]) -> str:
    """Best-effort extraction of the requested path for the 404 body.

    API Gateway prefixes the raw path with the stage name (e.g. `/dev/foo`
    for stage `dev`); strip it so the 404 body echoes the path the client
    actually requested via the custom domain.
    """
    ctx = event.get("requestContext") or {}
    http = ctx.get("http") or {}
    path = http.get("path") or event.get("rawPath") or "/"
    stage = ctx.get("stage") or ""
    if stage and stage != "$default":
        prefix = f"/{stage}"
        if path == prefix:
            return "/"
        if path.startswith(prefix + "/"):
            return path[len(prefix) :]
    return path


def _route_key(event: dict[str, Any]) -> str:
    """Return the matched route key (e.g. `GET /`, `$default`)."""
    return (event.get("requestContext") or {}).get("routeKey") or event.get("routeKey") or ""


def _html(status: int, body: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": _HTML_CT, "cache-control": "no-store"},
        "body": body,
    }


def _json(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": _JSON_CT, "cache-control": "no-store"},
        "body": json.dumps(payload),
    }


def _index_payload() -> dict[str, Any]:
    return {
        "name": catalog.NAME,
        "description": catalog.DESCRIPTION,
        "status": "ok",
        "endpoints": catalog.endpoints_map(),
        "source": catalog.SOURCE_URL,
        "dashboard": catalog.DASHBOARD_URL,
    }


def _not_found(event: dict[str, Any]) -> dict[str, Any]:
    path = _request_path(event)
    if _wants_html(event):
        return _html(404, render.not_found_html(path))
    return _json(
        404,
        {"error": "not_found", "hint": "GET / for the route index", "path": path},
    )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway v2 proxy entry point."""
    route = _route_key(event)
    path = _request_path(event)

    # /health: cheap liveness for the index service only.
    if route == "GET /health" or path == "/health":
        return _json(200, {"status": "ok", "service": "api-index"})

    # GET /: content-negotiated root index.
    if route == "GET /" or path == "/":
        if _wants_html(event):
            return _html(200, render.landing_html())
        return _json(200, _index_payload())

    # $default / anything unmatched: friendly 404.
    return _not_found(event)
