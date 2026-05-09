"""Unit tests for the `audit_lifecycle` audit-before-AND-after wrapper."""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from moto import mock_aws

from panakoes_admin_api.audit import audit_lifecycle


@pytest.fixture
def audit_table() -> Iterator:  # type: ignore[no-untyped-def]
    """Moto-backed audit-log table mirroring the dev module shape."""
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        table = ddb.create_table(
            TableName="panakoes-test-audit-log",
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
        table.wait_until_exists()
        yield table


def _all_items(table) -> list[dict]:  # type: ignore[no-untyped-def]
    response = table.scan()
    return response.get("Items", [])


@pytest.mark.unit
async def test_audit_writes_intent_before_command_runs(audit_table) -> None:  # type: ignore[no-untyped-def]
    """The intent row exists in the table before the body of the with-block runs."""
    intent_seen: list[str] = []
    async with audit_lifecycle(
        audit_table=audit_table,
        actor_id="user_admin",
        tier3_action="terminate-session",
        target={"session_id": "sess_123"},
        reason="incident response",
    ) as request_id:
        # Inside the body: intent must already be persisted.
        items = _all_items(audit_table)
        intent_rows = [i for i in items if i["action"].endswith(".intent")]
        assert len(intent_rows) == 1
        assert intent_rows[0]["request_id"] == request_id
        assert intent_rows[0]["outcome"] == "pending"
        intent_seen.append(request_id)

    assert intent_seen, "request_id should have been yielded to the body"


@pytest.mark.unit
async def test_audit_writes_outcome_success_after_command_runs(audit_table) -> None:  # type: ignore[no-untyped-def]
    """A successful body produces an `outcome` row with outcome=success."""
    async with audit_lifecycle(
        audit_table=audit_table,
        actor_id="user_admin",
        tier3_action="terminate-session",
        target={"session_id": "sess_123"},
        reason="incident response",
    ):
        pass  # body succeeds

    items = _all_items(audit_table)
    outcome_rows = [i for i in items if i["action"].endswith(".outcome")]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"] == "success"
    assert outcome_rows[0].get("error_message") is None


@pytest.mark.unit
async def test_audit_writes_outcome_failure_when_body_raises(audit_table) -> None:  # type: ignore[no-untyped-def]
    """A raising body produces an `outcome` row with outcome=failure + error message."""
    with pytest.raises(RuntimeError, match="boom"):
        async with audit_lifecycle(
            audit_table=audit_table,
            actor_id="user_admin",
            tier3_action="terminate-session",
            target={"session_id": "sess_123"},
            reason="incident response",
        ):
            raise RuntimeError("boom")

    items = _all_items(audit_table)
    outcome_rows = [i for i in items if i["action"].endswith(".outcome")]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"] == "failure"
    assert "RuntimeError: boom" in outcome_rows[0]["error_message"]


@pytest.mark.unit
async def test_audit_intent_and_outcome_share_request_id(audit_table) -> None:  # type: ignore[no-untyped-def]
    """The intent row and outcome row are joinable via `request_id`."""
    async with audit_lifecycle(
        audit_table=audit_table,
        actor_id="user_admin",
        tier3_action="terminate-session",
        target={"session_id": "sess_123"},
        reason="ops drill",
    ) as request_id:
        pass

    items = _all_items(audit_table)
    request_ids = {i["request_id"] for i in items}
    assert request_ids == {request_id}
    assert len(items) == 2  # exactly intent + outcome


@pytest.mark.unit
async def test_audit_tags_tier3_action_for_gsi(audit_table) -> None:  # type: ignore[no-untyped-def]
    """Both rows carry `tier3_action` so the Tier3ActionIndex GSI surfaces them."""
    async with audit_lifecycle(
        audit_table=audit_table,
        actor_id="user_admin",
        tier3_action="terminate-session",
        target={"session_id": "sess_123"},
        reason="ops drill",
    ):
        pass

    items = _all_items(audit_table)
    assert all(i["tier3_action"] == "terminate-session" for i in items)
