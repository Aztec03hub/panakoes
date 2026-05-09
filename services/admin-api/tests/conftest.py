"""Shared pytest fixtures for the admin-api service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _disable_otel_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin `OTEL_SDK_DISABLED=true` so the lifespan installs NoOp providers."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    yield


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Session-scoped event loop so async session fixtures stay alive."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    """Async httpx client bound to the FastAPI app via ASGITransport."""
    from httpx import ASGITransport, AsyncClient

    from panakoes_admin_api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto mocks for AWS services this service touches."""
    from moto import mock_aws

    with mock_aws():
        yield
