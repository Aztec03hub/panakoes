"""Smoke tests for the admin-api OpenAPI schema.

These tests pin the contract that downstream tooling (admin SPA TS
client codegen, external integrators reading the checked-in
`services/admin-api/openapi.json`) relies on. The path-presence
assertions catch the common breakage where a route is renamed or
deleted without updating the SPA.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

EXPECTED_PATHS = (
    "/api/v1/admin/audit-log",
    "/api/v1/admin/sessions/{session_id}/terminate",
    "/api/v1/admin/api-keys/{api_key_id}/revoke",
    "/api/v1/admin/streaming-sessions/{session_id}/kill",
    "/api/v1/admin/batch-jobs/{job_id}/kill",
    "/api/v1/admin/tenants/{tenant_id}/block",
    "/api/v1/admin/users/{user_id}/block-sessions",
    "/health",
)


@pytest.mark.integration
async def test_openapi_schema_advertises_expected_paths(
    async_client: AsyncClient,
) -> None:
    """The live `/openapi.json` lists every admin route the SPA depends on."""
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
