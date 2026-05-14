"""Root conftest: fixtures shared across all Panakoes services.

Provides a `localstack_available` session-scoped fixture that is True when
`AWS_ENDPOINT_URL` is set and the LocalStack health endpoint responds. Tests
that require real AWS resources should be marked `@pytest.mark.integration`
and guard themselves with `localstack_available`.
"""
import os
import pytest
import urllib.request


def _localstack_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/_localstack/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def localstack_url() -> str | None:
    url = os.getenv("AWS_ENDPOINT_URL", "")
    return url if url and _localstack_healthy(url) else None


@pytest.fixture(scope="session")
def localstack_available(localstack_url: str | None) -> bool:
    return localstack_url is not None
