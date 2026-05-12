"""Shared pytest fixtures for the Cost Rollup Aggregator Lambda.

Two test seams:

- `rollup_store`: a moto-backed `panakoes-test-tenant-cost-rollup`
  table wrapped in the real `TenantRollupStore` from cost-api. We
  exercise the production write path so an upstream change to the
  store contract is caught here, not at runtime.
- `FakeCEClient`: a hand-rolled minimal stand-in for boto3's
  `CostExplorerClient`. The real boto3 surface is not stubbable via
  `moto` for `get_cost_and_usage` (moto's CE coverage is shallow), so
  we build a small fake that records every call's kwargs and returns
  a queued response. This mirrors the `EventBridgeSpy` pattern used
  in `services/event-router/tests/conftest.py`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

# Pin AWS credentials BEFORE any boto3 import path runs. moto wants
# *something* in the slots so client init does not try to reach IMDS.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


TEST_TABLE_NAME = "panakoes-test-tenant-cost-rollup"
TEST_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin env vars to deterministic test values for every test."""
    monkeypatch.setenv("TENANT_COST_ROLLUP_TABLE", TEST_TABLE_NAME)
    monkeypatch.setenv("AWS_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    yield


@pytest.fixture
def aws_mocks() -> Iterator[None]:
    """Activate moto for DynamoDB; CE is faked separately."""
    with mock_aws():
        yield


@pytest.fixture
def rollup_store(aws_mocks: None) -> Any:
    """A fresh moto-backed rollup table wrapped in the real store class."""
    assert aws_mocks is None  # consume the fixture so moto stays active
    from panakoes_cost_api.tenant_rollup import TenantRollupStore

    ddb = boto3.resource("dynamodb", region_name=TEST_REGION)
    table = ddb.create_table(
        TableName=TEST_TABLE_NAME,
        KeySchema=[
            {"AttributeName": "tenant_id", "KeyType": "HASH"},
            {"AttributeName": "day_service", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "day_service", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return TenantRollupStore(table=table)


class FakeCEClient:
    """Boto3-shaped stand-in for the Cost Explorer client.

    Tests queue one or more `get_cost_and_usage` responses; calls are
    served FIFO. Every call's kwargs are recorded so tests can assert
    on the exact `TimePeriod` / `GroupBy` / `NextPageToken` shape the
    aggregator sent.
    """

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses: list[dict[str, Any]] = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, response: dict[str, Any]) -> None:
        """Push another response onto the FIFO queue."""
        self._responses.append(response)

    def get_cost_and_usage(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                "FakeCEClient: no queued responses; tests must queue one per expected call"
            )
        return self._responses.pop(0)


def make_ce_response(
    *,
    day_iso: str,
    tenant_groups: list[tuple[str, str]] | None = None,
    tenant_service_groups: list[tuple[str, str, str]] | None = None,
    untagged_amount: str | None = None,
    untagged_service: str = "Amazon Simple Storage Service",
    next_page_token: str | None = None,
    default_service: str = "Amazon Elastic Compute Cloud - Compute",
) -> dict[str, Any]:
    """Build a CE `GetCostAndUsage` response shape for tests.

    Post-ADR-040 the aggregator queries CE with two GroupBy dimensions
    (TAG `tenant_id`, DIMENSION `SERVICE`), so each group's `Keys` is
    `[tenant_id$<value>, <service>]`. Two ergonomic call shapes:

    - `tenant_groups=[(tenant_id, usd_amount), ...]`: legacy two-tuple
      shape; each row gets `default_service` as the second key so older
      tests keep working without re-writing every fixture.
    - `tenant_service_groups=[(tenant_id, service, usd_amount), ...]`:
      explicit three-tuple shape for tests that pin the service slot.

    `untagged_amount` produces a group with empty `tenant_id$` (CE's
    representation of untagged spend) and `untagged_service` as the
    second key.
    """
    groups: list[dict[str, Any]] = []
    for tenant_id, amount in tenant_groups or []:
        groups.append(
            {
                "Keys": [f"tenant_id${tenant_id}", default_service],
                "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}},
            }
        )
    for tenant_id, service, amount in tenant_service_groups or []:
        groups.append(
            {
                "Keys": [f"tenant_id${tenant_id}", service],
                "Metrics": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}},
            }
        )
    if untagged_amount is not None:
        groups.append(
            {
                "Keys": ["tenant_id$", untagged_service],
                "Metrics": {"UnblendedCost": {"Amount": untagged_amount, "Unit": "USD"}},
            }
        )
    response: dict[str, Any] = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": day_iso, "End": day_iso},
                "Total": {},
                "Groups": groups,
                "Estimated": False,
            }
        ],
    }
    if next_page_token is not None:
        response["NextPageToken"] = next_page_token
    return response
