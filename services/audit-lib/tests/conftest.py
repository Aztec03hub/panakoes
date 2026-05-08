"""Shared pytest fixtures for the panakoes-audit library.

Two main fixtures:

- `clean_audit_env`: scrubs `AUDIT_*` env vars and resets the global
  store between tests so config tests start from a known baseline.
- `dynamodb_table`: creates a fresh moto-backed DynamoDB table per
  test, so integration tests can write and read without spinning up
  real AWS infrastructure.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_dynamodb.service_resource import Table

from panakoes_audit._api import reset_store

AUDIT_ENV_KEYS = ("AUDIT_BACKEND", "AUDIT_TABLE_NAME", "AUDIT_AWS_REGION")


@pytest.fixture(autouse=True)
def clean_audit_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove `AUDIT_*` env vars and reset the global store per test."""
    for key in AUDIT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_store()
    yield
    reset_store()


@pytest.fixture
def dynamodb_table() -> Iterator[Table]:
    """Provision an in-memory DynamoDB table for the duration of a test."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="panakoes-audit-log",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield resource.Table("panakoes-audit-log")
