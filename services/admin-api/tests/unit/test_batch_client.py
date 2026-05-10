"""Unit tests for the Boto3 Batch terminator wrapper."""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_admin_api.batch_client import Boto3BatchTerminator


class _StubClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def terminate_job(self, *, jobId: str, reason: str) -> dict[str, Any]:
        self.calls.append({"jobId": jobId, "reason": reason})
        return {"ResponseMetadata": {"RequestId": "req-test"}}


@pytest.mark.unit
def test_terminate_job_passes_through_and_returns_request_id() -> None:
    client = _StubClient()
    terminator = Boto3BatchTerminator(client=client)  # type: ignore[arg-type]
    request_id = terminator.terminate_job(job_id="job-1", reason="ops drill")
    assert request_id == "req-test"
    assert client.calls == [{"jobId": "job-1", "reason": "ops drill"}]
