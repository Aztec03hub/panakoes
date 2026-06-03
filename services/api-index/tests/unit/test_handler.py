"""Unit tests for the api-index Lambda handler.

Covers the three behaviors of the service: content-negotiated root,
the cheap /health probe, and the friendly 404. The tests build API
Gateway v2 (payload format 2.0) events explicitly so the exact shape
the Lambda sees is visible inline.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from panakoes_api_index import catalog
from panakoes_api_index.handler import lambda_handler


def _event(
    *,
    route_key: str,
    path: str,
    accept: str | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    if accept is not None:
        headers["accept"] = accept
    method = route_key.split(" ")[0] if " " in route_key else "GET"
    return {
        "version": "2.0",
        "rawPath": path,
        "headers": headers,
        "requestContext": {
            "routeKey": route_key,
            "http": {"method": method, "path": path},
        },
    }


# ---------------------------------------------------------------------------
# GET / content negotiation
# ---------------------------------------------------------------------------


def test_root_browser_returns_html() -> None:
    event = _event(route_key="GET /", path="/", accept="text/html,application/xhtml+xml")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 200
    assert resp["headers"]["content-type"].startswith("text/html")
    body = resp["body"]
    assert "<!doctype html>" in body
    assert "Panakoes" in body
    assert catalog.SOURCE_URL in body
    assert catalog.DASHBOARD_URL in body
    # The live status badge fetch targets the auth health probe path.
    assert catalog.HEALTH_PROBE_PATH in body
    # Every catalog route renders into the table.
    for route, _desc in catalog.ENDPOINTS:
        assert route in body


def test_root_json_when_not_browser() -> None:
    event = _event(route_key="GET /", path="/", accept="application/json")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 200
    assert resp["headers"]["content-type"] == "application/json"
    body = json.loads(resp["body"])
    assert body["name"] == "panakoes-api"
    assert body["status"] == "ok"
    assert body["source"] == catalog.SOURCE_URL
    assert body["dashboard"] == catalog.DASHBOARD_URL
    assert body["description"] == catalog.DESCRIPTION
    # JSON endpoints map must match the catalog exactly (no drift).
    assert body["endpoints"] == catalog.endpoints_map()


def test_root_json_when_no_accept_header() -> None:
    event = _event(route_key="GET /", path="/")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 200
    assert resp["headers"]["content-type"] == "application/json"


def test_root_matches_on_path_without_route_key() -> None:
    # Direct invocation / format-1.0-ish event with only rawPath.
    event = {"rawPath": "/", "headers": {"Accept": "text/html"}}
    resp = lambda_handler(event)
    assert resp["statusCode"] == 200
    assert resp["headers"]["content-type"].startswith("text/html")


def test_accept_header_case_insensitive_key() -> None:
    event = {"rawPath": "/", "headers": {"ACCEPT": "text/html"}}
    resp = lambda_handler(event)
    assert resp["headers"]["content-type"].startswith("text/html")


def test_accept_header_none_value_is_treated_as_json() -> None:
    event = {"rawPath": "/", "headers": {"accept": None}}
    resp = lambda_handler(event)
    assert resp["headers"]["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


def test_health_returns_service_ok() -> None:
    event = _event(route_key="GET /health", path="/health")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"status": "ok", "service": "api-index"}


def test_health_matches_on_path() -> None:
    event = {"rawPath": "/health", "headers": {}}
    resp = lambda_handler(event)
    body = json.loads(resp["body"])
    assert body["service"] == "api-index"


# ---------------------------------------------------------------------------
# $default / 404
# ---------------------------------------------------------------------------


def test_unknown_path_json_404() -> None:
    event = _event(route_key="$default", path="/v1/does-not-exist", accept="application/json")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 404
    assert resp["headers"]["content-type"] == "application/json"
    body = json.loads(resp["body"])
    assert body["error"] == "not_found"
    assert body["hint"] == "GET / for the route index"
    assert body["path"] == "/v1/does-not-exist"


def test_unknown_path_html_404_for_browser() -> None:
    event = _event(route_key="$default", path="/nope", accept="text/html")
    resp = lambda_handler(event)

    assert resp["statusCode"] == 404
    assert resp["headers"]["content-type"].startswith("text/html")
    assert "404" in resp["body"]
    assert "/nope" in resp["body"]


def test_404_path_is_html_escaped() -> None:
    event = _event(route_key="$default", path="/<script>x</script>", accept="text/html")
    resp = lambda_handler(event)
    assert "<script>x</script>" not in resp["body"]
    assert "&lt;script&gt;" in resp["body"]


def test_404_includes_requested_path_from_http_context() -> None:
    event = {
        "requestContext": {"routeKey": "$default", "http": {"path": "/weird"}},
        "headers": {},
    }
    resp = lambda_handler(event)
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert body["path"] == "/weird"


def test_other_headers_present_but_no_accept_returns_json() -> None:
    # Exercises the _wants_html loop iterating past a non-accept header
    # and falling through to the default (no accept) JSON path.
    event = {"rawPath": "/", "headers": {"x-forwarded-for": "1.2.3.4"}}
    resp = lambda_handler(event)
    assert resp["headers"]["content-type"] == "application/json"


def test_context_arg_is_accepted_and_ignored() -> None:
    event = _event(route_key="GET /health", path="/health")
    resp = lambda_handler(event, context=object())
    assert resp["statusCode"] == 200


@pytest.mark.parametrize("accept", ["*/*", "application/xml", ""])
def test_non_html_accept_values_get_json(accept: str) -> None:
    event = _event(route_key="GET /", path="/", accept=accept)
    resp = lambda_handler(event)
    assert resp["headers"]["content-type"] == "application/json"
