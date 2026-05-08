"""Smoke test for the `/health` endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.integration
async def test_health_endpoint_returns_ok() -> None:
    """`GET /health` returns HTTP 200 with the expected payload.

    This test deliberately does NOT depend on the moto / EC2 fixtures;
    `/health` must be reachable even when AWS is unavailable.
    """
    from panakoes_gpu_spawner.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "gpu-spawner"}


@pytest.mark.integration
async def test_health_does_not_require_auth() -> None:
    """`/health` is accessible without an Authorization header."""
    from panakoes_gpu_spawner.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")
    assert response.status_code == 200
