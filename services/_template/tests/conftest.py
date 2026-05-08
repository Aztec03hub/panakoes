"""Shared pytest fixtures for the template service.

Provides the standard test surface every Panakoes Python service inherits:
async HTTP client wired to the FastAPI app, lazy testcontainers fixtures
(Postgres, Redis), and moto-backed AWS mocks. Container fixtures are
session-scoped and only spin up when a test actually requests them, so
unit-only runs stay fast.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Session-scoped event loop so async session fixtures stay alive."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncIterator[AsyncClient]:
    """Async httpx client bound to the FastAPI app via ASGITransport.

    Use for in-process integration tests. No real network is involved; the
    app is invoked directly through the ASGI interface.
    """
    from httpx import ASGITransport, AsyncClient

    from template_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[Any]:
    """Spin up a real Postgres testcontainer for the test session.

    Per ADR-018 we do NOT mock the database in integration tests. Tests
    requesting this fixture get the connection URL via `get_connection_url()`.
    The container is reused across tests in the session for speed.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Iterator[Any]:
    """Spin up a real Redis testcontainer for the test session.

    Tests requesting this fixture get the host and port via the standard
    testcontainers accessors. Container is reused across the session.
    """
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto mocks for the AWS services this service touches.

    Currently mocks S3 and DynamoDB. Extend on a per-service basis by
    overriding this fixture in the consuming service's conftest.
    """
    from moto import mock_aws

    with mock_aws():
        yield
