"""Smoke test for the `/health` endpoint via the in-process async client."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_health_endpoint_returns_ok(async_client: AsyncClient) -> None:
    """`GET /health` returns HTTP 200 with the expected status payload."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "billing"
