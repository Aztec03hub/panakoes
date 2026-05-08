"""Unit tests for `panakoes_billing.storage.dynamodb`.

We exercise the store against moto's DynamoDB so the schema (PK / SK
shapes, attribute coercion, query pagination) is real.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from panakoes_billing.storage.dynamodb import (
    BillingEventStore,
    _coerce_decimals,
    _event_sk,
    _user_pk,
)


@pytest.mark.unit
def test_user_pk_format() -> None:
    """`_user_pk` produces the documented `USER#<id>` shape."""
    assert _user_pk("u_42") == "USER#u_42"


@pytest.mark.unit
def test_event_sk_format() -> None:
    """`_event_sk` produces the documented `EVENT#<ulid>` shape."""
    assert _event_sk("01HABCXYZ") == "EVENT#01HABCXYZ"


@pytest.mark.unit
def test_coerce_decimals_passes_through_strings() -> None:
    """Strings, ints, floats, None, and bool round-trip unchanged."""
    assert _coerce_decimals("hello") == "hello"
    assert _coerce_decimals(42) == 42
    assert _coerce_decimals(None) is None
    assert _coerce_decimals(True) is True


@pytest.mark.unit
def test_coerce_decimals_collapses_integer_decimals() -> None:
    """Integer-valued Decimals become plain ints."""
    assert _coerce_decimals(Decimal("5")) == 5


@pytest.mark.unit
def test_coerce_decimals_collapses_float_decimals() -> None:
    """Non-integer Decimals become floats."""
    assert _coerce_decimals(Decimal("1.5")) == pytest.approx(1.5)


@pytest.mark.unit
def test_coerce_decimals_walks_nested_structures() -> None:
    """Dicts and lists are walked recursively."""
    src: dict[str, Any] = {
        "x": Decimal("3"),
        "y": [Decimal("1"), Decimal("2.5"), {"z": Decimal("4")}],
    }
    result = _coerce_decimals(src)
    assert result == {"x": 3, "y": [1, pytest.approx(2.5), {"z": 4}]}


@pytest.mark.unit
def test_store_table_name_property(dynamodb_table: Any) -> None:
    """`BillingEventStore.table_name` returns the configured table name."""
    assert dynamodb_table is not None
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    assert store.table_name == TEST_TABLE_NAME


@pytest.mark.unit
def test_store_append_returns_ulid_and_writes_item(dynamodb_table: Any) -> None:
    """`append` returns the event id and persists the documented shape."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    event_id = store.append(
        user_id="u_1",
        event_type="checkout_started",
        attributes={"tier": "pro", "seats": 1, "stripe_session_id": "cs_1"},
    )
    assert len(event_id) == 26

    response = dynamodb_table.scan()
    items = response["Items"]
    assert len(items) == 1
    item = items[0]
    assert item["pk"] == "USER#u_1"
    assert item["sk"] == f"EVENT#{event_id}"
    assert item["event_type"] == "checkout_started"
    assert item["tier"] == "pro"
    assert item["seats"] == 1


@pytest.mark.unit
def test_store_append_protects_identity_keys(dynamodb_table: Any) -> None:
    """Caller-supplied `pk`, `sk`, `event_id`, etc. are silently ignored."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    event_id = store.append(
        user_id="u_2",
        event_type="checkout_completed",
        attributes={
            "pk": "MALICIOUS",
            "sk": "MALICIOUS",
            "event_id": "MALICIOUS",
            "user_id": "spoofed",
            "event_type": "spoofed_type",
            "created_at": "1970-01-01T00:00:00+00:00",
            "tier": "team",
        },
    )

    response = dynamodb_table.scan()
    items = response["Items"]
    assert len(items) == 1
    item = items[0]
    assert item["pk"] == "USER#u_2"
    assert item["sk"] == f"EVENT#{event_id}"
    assert item["event_id"] == event_id
    assert item["user_id"] == "u_2"
    assert item["event_type"] == "checkout_completed"
    assert item["created_at"] != "1970-01-01T00:00:00+00:00"
    assert item["tier"] == "team"


@pytest.mark.unit
def test_store_append_with_no_attributes(dynamodb_table: Any) -> None:
    """`attributes=None` writes only the protected base fields."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    event_id = store.append(user_id="u_3", event_type="invoice_paid", attributes=None)
    response = dynamodb_table.scan()
    items = response["Items"]
    assert len(items) == 1
    assert items[0]["event_id"] == event_id
    assert "tier" not in items[0]


@pytest.mark.unit
def test_store_latest_event_returns_none_for_unknown_user(dynamodb_table: Any) -> None:
    """`latest_event` returns `None` when no events exist for the user."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    assert store.latest_event("nonexistent_user") is None


@pytest.mark.unit
def test_store_latest_event_returns_most_recent(dynamodb_table: Any) -> None:
    """`latest_event` returns the highest-sort-key event."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(user_id="u_4", event_type="checkout_started", attributes={"tier": "pro"})
    second = store.append(
        user_id="u_4",
        event_type="checkout_completed",
        attributes={"tier": "pro", "stripe_customer_id": "cus_42"},
    )

    latest = store.latest_event("u_4")
    assert latest is not None
    assert latest["event_id"] == second
    assert latest["event_type"] == "checkout_completed"


@pytest.mark.unit
def test_store_latest_subscription_event_skips_non_subscription_events(
    dynamodb_table: Any,
) -> None:
    """`latest_subscription_event` walks past non-subscription events."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    sub_event = store.append(
        user_id="u_5",
        event_type="subscription_updated",
        attributes={"tier": "pro", "status": "active"},
    )
    # Newer event is not a subscription event; must be skipped.
    store.append(
        user_id="u_5",
        event_type="checkout_started",
        attributes={"tier": "team", "seats": 5},
    )

    latest_sub = store.latest_subscription_event("u_5")
    assert latest_sub is not None
    assert latest_sub["event_id"] == sub_event
    assert latest_sub["event_type"] == "subscription_updated"


@pytest.mark.unit
def test_store_latest_subscription_event_returns_none_when_none_exist(
    dynamodb_table: Any,
) -> None:
    """`latest_subscription_event` returns None when only non-sub events exist."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(user_id="u_6", event_type="checkout_started")
    assert store.latest_subscription_event("u_6") is None


@pytest.mark.unit
def test_store_latest_subscription_event_paginates(dynamodb_table: Any) -> None:
    """`latest_subscription_event` keeps paging until it finds a hit or exhausts."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    target = store.append(
        user_id="u_7",
        event_type="invoice_paid",
        attributes={"tier": "pro", "status": "active"},
    )
    # Add 30 newer non-subscription events to force pagination (Limit=25).
    for _ in range(30):
        store.append(user_id="u_7", event_type="checkout_started")

    latest = store.latest_subscription_event("u_7")
    assert latest is not None
    assert latest["event_id"] == target


@pytest.mark.unit
def test_store_find_customer_id_returns_most_recent(dynamodb_table: Any) -> None:
    """`find_customer_id` returns the newest customer id on file."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="u_8",
        event_type="checkout_completed",
        attributes={"stripe_customer_id": "cus_old"},
    )
    store.append(
        user_id="u_8",
        event_type="subscription_updated",
        attributes={"stripe_customer_id": "cus_new"},
    )
    assert store.find_customer_id("u_8") == "cus_new"


@pytest.mark.unit
def test_store_find_customer_id_returns_none_when_absent(dynamodb_table: Any) -> None:
    """`find_customer_id` returns None when the user has no customer recorded."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(user_id="u_9", event_type="checkout_started")
    assert store.find_customer_id("u_9") is None


@pytest.mark.unit
def test_store_find_customer_id_paginates(dynamodb_table: Any) -> None:
    """`find_customer_id` keeps paging until it finds a customer id or exhausts."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    store = BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    store.append(
        user_id="u_10",
        event_type="checkout_completed",
        attributes={"stripe_customer_id": "cus_buried"},
    )
    for _ in range(30):
        store.append(user_id="u_10", event_type="checkout_started")
    assert store.find_customer_id("u_10") == "cus_buried"


@pytest.mark.unit
def test_store_uses_injected_resource_without_calling_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing `resource=...` skips the `boto3.resource` call.

    Confirms the dependency-injection seam tests rely on works.
    """
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    boto3_called = False

    def _explode(*_: Any, **__: Any) -> None:
        nonlocal boto3_called
        boto3_called = True

    class _StubResource:
        def Table(self, name: str) -> object:
            assert name == TEST_TABLE_NAME
            return object()

    monkeypatch.setattr("panakoes_billing.storage.dynamodb.boto3.resource", _explode)
    BillingEventStore(
        table_name=TEST_TABLE_NAME,
        region_name=TEST_REGION,
        resource=_StubResource(),  # type: ignore[arg-type]
    )
    assert boto3_called is False


@pytest.mark.unit
def test_store_default_resource_uses_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """`BillingEventStore` constructs a boto3 resource when `resource=None`."""
    from tests.conftest import TEST_REGION, TEST_TABLE_NAME

    captured: dict[str, Any] = {}

    class _StubResource:
        def Table(self, name: str) -> object:
            captured["table"] = name
            return object()

    def _fake_boto3_resource(service: str, region_name: str) -> _StubResource:
        captured["service"] = service
        captured["region"] = region_name
        return _StubResource()

    monkeypatch.setattr(
        "panakoes_billing.storage.dynamodb.boto3.resource", _fake_boto3_resource
    )
    BillingEventStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    assert captured == {
        "service": "dynamodb",
        "region": TEST_REGION,
        "table": TEST_TABLE_NAME,
    }
