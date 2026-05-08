"""Shared pytest fixtures for the panakoes-test-helpers self-tests.

Test isolation: every test runs without AWS credentials in the
environment so a stray boto3 call would error out loudly instead of
silently hitting real AWS. moto-backed tests provision dummy creds
inside their `mock_aws()` block.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _scrub_aws_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip any real AWS credentials before every test."""
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    # moto requires *something* in the credential slots; provide deterministic
    # placeholders that are obviously not real.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    yield
