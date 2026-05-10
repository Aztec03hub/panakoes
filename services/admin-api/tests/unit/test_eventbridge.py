"""Unit tests for the Boto3 EventBridge publisher wrapper."""

from __future__ import annotations

import json
from typing import Any

import pytest

from panakoes_admin_api.eventbridge import Boto3EventBridgePublisher


class _StubClient:
    def __init__(self, *, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls.append({"Entries": Entries})
        return self.response


@pytest.mark.unit
def test_put_event_returns_event_id_and_serializes_detail() -> None:
    client = _StubClient(
        response={
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "evt-abc"}],
        }
    )
    publisher = Boto3EventBridgePublisher(client=client, bus_name="panakoes-dev")  # type: ignore[arg-type]
    event_id = publisher.put_event(
        source="panakoes.admin-api",
        detail_type="X",
        detail={"a": 1},
    )
    assert event_id == "evt-abc"
    assert client.calls[0]["Entries"][0]["EventBusName"] == "panakoes-dev"
    assert json.loads(client.calls[0]["Entries"][0]["Detail"]) == {"a": 1}


@pytest.mark.unit
def test_put_event_raises_when_failed_entry_count_nonzero() -> None:
    client = _StubClient(
        response={
            "FailedEntryCount": 1,
            "Entries": [{"ErrorMessage": "bad"}],
        }
    )
    publisher = Boto3EventBridgePublisher(client=client, bus_name="panakoes-dev")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="rejected 1 entry"):
        publisher.put_event(source="s", detail_type="t", detail={})


@pytest.mark.unit
def test_put_event_raises_when_no_entries_returned() -> None:
    client = _StubClient(response={"FailedEntryCount": 0, "Entries": []})
    publisher = Boto3EventBridgePublisher(client=client, bus_name="panakoes-dev")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no entries"):
        publisher.put_event(source="s", detail_type="t", detail={})


@pytest.mark.unit
def test_put_event_raises_when_entry_missing_event_id() -> None:
    client = _StubClient(
        response={"FailedEntryCount": 0, "Entries": [{"EventId": ""}]}
    )
    publisher = Boto3EventBridgePublisher(client=client, bus_name="panakoes-dev")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="no EventId"):
        publisher.put_event(source="s", detail_type="t", detail={})
