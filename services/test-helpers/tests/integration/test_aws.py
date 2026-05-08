"""Integration tests for `panakoes_test_helpers.aws` against moto."""

from __future__ import annotations

import boto3
import pytest

from panakoes_test_helpers.aws import (
    dynamodb_test_table,
    eventbridge_test_bus,
    s3_test_bucket,
)


@pytest.mark.integration
def test_s3_test_bucket_creates_bucket_with_versioning_enabled() -> None:
    """Versioning is on after the helper provisions the bucket."""
    with s3_test_bucket("panakoes-test-bucket") as name:
        client = boto3.client("s3", region_name="us-east-1")
        response = client.get_bucket_versioning(Bucket=name)
        assert response["Status"] == "Enabled"


@pytest.mark.integration
def test_s3_test_bucket_blocks_public_access() -> None:
    """All four public-access-block settings are true."""
    with s3_test_bucket("panakoes-test-bucket") as name:
        client = boto3.client("s3", region_name="us-east-1")
        response = client.get_public_access_block(Bucket=name)
        cfg = response["PublicAccessBlockConfiguration"]
        assert cfg["BlockPublicAcls"] is True
        assert cfg["IgnorePublicAcls"] is True
        assert cfg["BlockPublicPolicy"] is True
        assert cfg["RestrictPublicBuckets"] is True


@pytest.mark.integration
def test_s3_test_bucket_yields_the_caller_supplied_name() -> None:
    """The yielded value matches the input so test bodies have a single source of truth."""
    with s3_test_bucket("custom-name-abc") as name:
        assert name == "custom-name-abc"


@pytest.mark.integration
def test_s3_test_bucket_rejects_empty_name() -> None:
    """An empty bucket name is a programming error; surface it loudly."""
    with pytest.raises(ValueError, match="bucket name"):  # noqa: SIM117 - testing context manager error path
        with s3_test_bucket(""):
            pass


@pytest.mark.integration
def test_s3_test_bucket_supports_putting_and_getting_objects() -> None:
    """End-to-end self-test: the bucket actually accepts an object."""
    with s3_test_bucket("rt-bucket") as name:
        client = boto3.client("s3", region_name="us-east-1")
        client.put_object(Bucket=name, Key="hello.txt", Body=b"hello world")
        body = client.get_object(Bucket=name, Key="hello.txt")["Body"].read()
        assert body == b"hello world"


@pytest.mark.integration
def test_dynamodb_test_table_creates_table_with_pay_per_request() -> None:
    """Billing mode is fixed at PAY_PER_REQUEST."""
    schema = [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    with dynamodb_test_table("panakoes-test-ddb", schema) as table:
        client = boto3.client("dynamodb", region_name="us-east-1")
        response = client.describe_table(TableName=table.table_name)
        assert response["Table"]["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"


@pytest.mark.integration
def test_dynamodb_test_table_supports_put_and_get() -> None:
    """End-to-end self-test: the helper-provisioned table accepts items."""
    schema = [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    with dynamodb_test_table("rt-ddb", schema) as table:
        table.put_item(Item={"pk": "USER#1", "sk": "META", "value": "ok"})
        response = table.get_item(Key={"pk": "USER#1", "sk": "META"})
        assert response["Item"]["value"] == "ok"


@pytest.mark.integration
def test_dynamodb_test_table_supports_global_secondary_index() -> None:
    """A GSI passed at construction time becomes queryable."""
    schema = [
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"},
    ]
    gsi = [
        {
            "IndexName": "by-status",
            "KeySchema": [
                {"AttributeName": "status", "KeyType": "HASH"},
                {"AttributeName": "created_at", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ]
    with dynamodb_test_table("rt-ddb-gsi", schema, gsi=gsi) as table:
        client = boto3.client("dynamodb", region_name="us-east-1")
        described = client.describe_table(TableName=table.table_name)
        indexes = described["Table"].get("GlobalSecondaryIndexes", [])
        names = {i["IndexName"] for i in indexes}
        assert "by-status" in names


@pytest.mark.integration
def test_dynamodb_test_table_rejects_empty_name() -> None:
    """An empty table name is a programming error."""
    with pytest.raises(ValueError, match="table name"):  # noqa: SIM117 - testing CM error path
        with dynamodb_test_table("", [{"AttributeName": "pk", "KeyType": "HASH"}]):
            pass


@pytest.mark.integration
def test_dynamodb_test_table_rejects_empty_key_schema() -> None:
    """At minimum, the partition key must be declared."""
    with pytest.raises(ValueError, match="key_schema"):  # noqa: SIM117 - testing CM error path
        with dynamodb_test_table("name-ok", []):
            pass


@pytest.mark.integration
def test_eventbridge_test_bus_creates_named_bus() -> None:
    """The bus is visible via list_event_buses inside the same mock context."""
    with eventbridge_test_bus("panakoes-test-bus") as name:
        client = boto3.client("events", region_name="us-east-1")
        response = client.list_event_buses()
        names = {b["Name"] for b in response["EventBuses"]}
        assert name in names


@pytest.mark.integration
def test_eventbridge_test_bus_supports_put_events() -> None:
    """End-to-end self-test: the bus accepts a put_events call."""
    with eventbridge_test_bus("rt-bus") as name:
        client = boto3.client("events", region_name="us-east-1")
        response = client.put_events(
            Entries=[
                {
                    "EventBusName": name,
                    "Source": "panakoes.test",
                    "DetailType": "test.event",
                    "Detail": '{"hello":"world"}',
                }
            ]
        )
        assert response["FailedEntryCount"] == 0


@pytest.mark.integration
def test_eventbridge_test_bus_rejects_empty_name() -> None:
    """An empty bus name is a programming error."""
    with pytest.raises(ValueError, match="bus name"):  # noqa: SIM117 - testing CM error path
        with eventbridge_test_bus(""):
            pass
