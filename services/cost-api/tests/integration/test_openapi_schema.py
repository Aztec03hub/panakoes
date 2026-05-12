"""Smoke tests for the cost-api OpenAPI schema.

These tests pin the contract that downstream tooling (admin SPA TS
client codegen, external integrators reading the checked-in
`services/cost-api/openapi.json`) relies on. The path-presence
assertions catch the common breakage where a route is renamed or
deleted without updating the SPA.

The `/docs` and `/openapi.json` route assertions cover the
`ENABLE_OPENAPI_DOCS` gating wired into `main.py`.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

EXPECTED_PATHS = (
    "/api/v1/cost/by-service",
    "/api/v1/cost/by-tenant",
    "/api/v1/cost/forecast",
    "/api/v1/cost/anomalies",
    "/health",
)


@pytest.mark.integration
async def test_openapi_schema_advertises_expected_paths(
    async_client: AsyncClient,
) -> None:
    """The live `/openapi.json` lists every cost route the SPA depends on."""
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.1")
    paths = set(schema["paths"].keys())
    for expected in EXPECTED_PATHS:
        assert expected in paths, f"missing route in openapi schema: {expected}"


@pytest.mark.integration
async def test_swagger_ui_is_served_when_docs_enabled(
    async_client: AsyncClient,
) -> None:
    """`/docs` returns Swagger UI HTML when the dev docs toggle is on."""
    response = await async_client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
