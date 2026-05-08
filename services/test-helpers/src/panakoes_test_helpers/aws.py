"""moto-backed AWS resource builders for Panakoes service tests.

Each helper here returns a context manager that activates `moto.mock_aws`,
provisions one resource with the project's standard hardening, yields a
handle (the bucket name, table object, or bus name) for the test body to
use, and cleans up on exit. They double as pytest fixtures via
`yield`-style fixture functions (callers can wrap with
`@pytest.fixture` if they want a long-lived fixture, or use the context
manager directly inline).

## Why we wrap moto

Three services already provision moto-backed S3 buckets and DynamoDB
tables in their `conftest.py`. The boilerplate is identical, but each
copy drifted slightly: one forgot to enable versioning, another forgot
public-access-block, a third used `BillingMode=PROVISIONED` and silently
skewed some throughput tests.

These helpers encode the project's standard hardening (S3 versioning +
public-access-block, DDB PAY_PER_REQUEST) so every service that adopts
them gets identical fixtures. When the standard changes (e.g. when we
add object-lock to S3), it changes here once.

## Why context managers, not bare functions

`mock_aws()` must be active for the duration of the test, not just the
provisioning step. A bare function that exited after `create_bucket`
would tear the mock down before the test body could read or write. The
context-manager shape forces the mock to stay open as long as the test
needs it, which is the actual lifecycle the test wants.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import boto3
from moto import mock_aws

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table


@contextmanager
def s3_test_bucket(name: str, *, region: str = "us-east-1") -> Iterator[str]:
    """Provision a hardened, moto-backed S3 bucket for a test.

    Hardening matches the project's standard for any new bucket
    (`infra/dev/data/` and equivalents):

    - Bucket versioning enabled.
    - Public access fully blocked (the four `BlockPublicAcls`,
      `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`
      settings all true).

    Yields the bucket name so test bodies can pass it straight to a
    boto3 client they create inside the same mock context. moto tears
    down the bucket and all its objects when the context exits.
    """
    if not name:
        raise ValueError("bucket name must be non-empty")
    with mock_aws():
        client = boto3.client("s3", region_name=region)
        if region == "us-east-1":
            # us-east-1 is the AWS default; passing LocationConstraint here
            # is rejected by both real S3 and moto. Every other region
            # requires the constraint.
            client.create_bucket(Bucket=name)
        else:  # pragma: no cover - we test the us-east-1 path; this branch is for parity
            # The boto3 stubs declare LocationConstraint as a literal of all
            # known regions; the helper accepts any string for forward
            # compatibility, so cast through Any at the call boundary.
            constraint: Any = region
            client.create_bucket(
                Bucket=name,
                CreateBucketConfiguration={"LocationConstraint": constraint},
            )
        client.put_bucket_versioning(
            Bucket=name,
            VersioningConfiguration={"Status": "Enabled"},
        )
        client.put_public_access_block(
            Bucket=name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        yield name


@contextmanager
def dynamodb_test_table(
    name: str,
    key_schema: list[dict[str, str]],
    gsi: list[dict[str, Any]] | None = None,
    *,
    region: str = "us-east-1",
) -> Iterator[Table]:
    """Provision a moto-backed DynamoDB table for a test.

    `key_schema` is the list of `{"AttributeName": ..., "KeyType": ...}`
    dicts boto3 expects (HASH plus optional RANGE). `gsi` is an optional
    list of GSI definitions in the same shape `create_table` accepts;
    passing `None` (the default) creates a table with no GSIs.

    Billing is fixed to `PAY_PER_REQUEST`; the project does not use
    provisioned capacity in dev or test, and accidentally introducing
    one via copy-paste has caused real cost spikes. Every test table
    matches that posture.

    The required `AttributeDefinitions` are derived automatically from
    `key_schema` and any `KeySchema` blocks inside `gsi`, so callers
    pass the keys once. Yields the boto3 `Table` resource ready to use.
    """
    if not name:
        raise ValueError("table name must be non-empty")
    if not key_schema:
        raise ValueError("key_schema must include at least the partition key")
    with mock_aws():
        client = boto3.client("dynamodb", region_name=region)
        attribute_definitions = _derive_attribute_definitions(key_schema, gsi)
        create_kwargs: dict[str, Any] = {
            "TableName": name,
            "KeySchema": key_schema,
            "AttributeDefinitions": attribute_definitions,
            "BillingMode": "PAY_PER_REQUEST",
        }
        if gsi:
            create_kwargs["GlobalSecondaryIndexes"] = gsi
        client.create_table(**create_kwargs)
        resource = boto3.resource("dynamodb", region_name=region)
        yield resource.Table(name)


def _derive_attribute_definitions(
    key_schema: list[dict[str, str]],
    gsi: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Collect every attribute referenced by `key_schema` or any GSI key.

    DynamoDB requires `AttributeDefinitions` to list exactly the
    attributes used as keys (not extras). Building this once from the
    inputs spares callers the duplicated bookkeeping and the cryptic
    `ValidationException` boto3 raises when the lists drift.
    """
    seen: dict[str, str] = {}
    for k in key_schema:
        seen.setdefault(k["AttributeName"], "S")
    for index in gsi or []:
        for k in index.get("KeySchema", []):
            seen.setdefault(k["AttributeName"], "S")
    return [
        {"AttributeName": name, "AttributeType": atype} for name, atype in seen.items()
    ]


@contextmanager
def eventbridge_test_bus(name: str, *, region: str = "us-east-1") -> Iterator[str]:
    """Provision a moto-backed EventBridge custom bus for a test.

    Yields the bus name. Tests typically pass it straight to a boto3
    `events` client they create inside the same `with` block; moto's
    in-memory state is shared across all clients in the same mock
    context.

    No retention or archival rules are configured here; tests that
    exercise archival should configure that explicitly so the test is
    self-contained and the helper does not encode an opinion that
    diverges from production-side IaC.
    """
    if not name:
        raise ValueError("bus name must be non-empty")
    with mock_aws():
        client = boto3.client("events", region_name=region)
        client.create_event_bus(Name=name)
        yield name
