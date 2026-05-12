"""Unit tests for ``panakoes_billing.storage.subscriptions``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from panakoes_billing.storage.subscriptions import SubscriptionStore

TABLE = "panakoes-dev-subscriptions-test"
REGION = "us-east-1"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Provision the table inside a moto mock and yield a bound store."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "tenant_id", "KeyType": "HASH"},
                {"AttributeName": "subscription_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "tenant_id", "AttributeType": "S"},
                {"AttributeName": "subscription_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield SubscriptionStore(table_name=TABLE, region_name=REGION)


@pytest.mark.unit
def test_upsert_inserts_new_row(store: SubscriptionStore) -> None:
    """A first upsert for a (tenant, subscription) pair writes the row."""
    applied = store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="active",
        current_period_end=datetime(2026, 6, 1, tzinfo=UTC),
        quantity=1,
        stripe_customer_id="cus_1",
    )
    assert applied is True
    assert store.get_plan("user_1") == "pro"


@pytest.mark.unit
def test_upsert_is_idempotent_on_same_event_id(store: SubscriptionStore) -> None:
    """Replaying the same event_id returns False and does not overwrite."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="active",
    )
    applied_again = store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="team",  # different plan, but same event id => no write
        status="active",
    )
    assert applied_again is False
    assert store.get_plan("user_1") == "pro"


@pytest.mark.unit
def test_upsert_with_new_event_id_overwrites(store: SubscriptionStore) -> None:
    """A subsequent event with a new event_id updates the row."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="active",
    )
    applied = store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_2",
        plan="team",
        status="active",
        quantity=5,
    )
    assert applied is True
    assert store.get_plan("user_1") == "team"


@pytest.mark.unit
def test_get_plan_returns_free_for_unknown_tenant(store: SubscriptionStore) -> None:
    """A tenant with no rows is on the implicit free plan."""
    assert store.get_plan("ghost") == "free"


@pytest.mark.unit
def test_canceled_subscription_drops_plan_to_free(store: SubscriptionStore) -> None:
    """A canceled status excludes the row from the effective plan."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="canceled",
    )
    assert store.get_plan("user_1") == "free"


@pytest.mark.unit
def test_multiple_subscriptions_pick_highest_plan(store: SubscriptionStore) -> None:
    """A tenant with both pro and team rows resolves to team."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_pro",
        event_id="evt_pro",
        plan="pro",
        status="active",
    )
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_team",
        event_id="evt_team",
        plan="team",
        status="active",
    )
    assert store.get_plan("user_1") == "team"


@pytest.mark.unit
def test_past_due_subscription_retains_access(store: SubscriptionStore) -> None:
    """`past_due` still counts as active by Stripe convention."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="past_due",
    )
    assert store.get_plan("user_1") == "pro"


@pytest.mark.unit
def test_get_plan_skips_rows_with_unknown_plan_value(store: SubscriptionStore) -> None:
    """A row carrying an unexpected `plan` value is ignored, not promoted."""
    # Write a raw row directly so we can stash a junk plan value.
    resource = boto3.resource("dynamodb", region_name=REGION)
    table = resource.Table(TABLE)
    table.put_item(
        Item={
            "tenant_id": "user_junk",
            "subscription_id": "sub_junk",
            "plan": "platinum",
            "status": "active",
            "last_event_id": "evt_junk",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    assert store.get_plan("user_junk") == "free"


@pytest.mark.unit
def test_table_name_property(store: SubscriptionStore) -> None:
    """`table_name` round-trips the constructor argument."""
    assert store.table_name == TABLE


@pytest.mark.unit
def test_upsert_reraises_unexpected_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-conditional ClientError propagates so the caller sees it."""
    from unittest.mock import MagicMock

    from botocore.exceptions import ClientError

    fake_resource = MagicMock()
    fake_table = MagicMock()
    fake_resource.Table.return_value = fake_table
    fake_table.put_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "boom"}},
        "PutItem",
    )
    s = SubscriptionStore(table_name="t", region_name="us-east-1", resource=fake_resource)
    with pytest.raises(ClientError):
        s.upsert(
            tenant_id="u",
            subscription_id="s",
            event_id="e",
            plan="pro",
            status="active",
        )


@pytest.mark.unit
def test_upsert_with_cancel_at_serialises_iso8601(store: SubscriptionStore) -> None:
    """A cancel_at datetime is serialised as ISO 8601."""
    store.upsert(
        tenant_id="user_1",
        subscription_id="sub_1",
        event_id="evt_1",
        plan="pro",
        status="active",
        cancel_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    resource = boto3.resource("dynamodb", region_name=REGION)
    table = resource.Table(TABLE)
    item = table.get_item(Key={"tenant_id": "user_1", "subscription_id": "sub_1"})["Item"]
    assert item["cancel_at"].startswith("2026-07-01")
