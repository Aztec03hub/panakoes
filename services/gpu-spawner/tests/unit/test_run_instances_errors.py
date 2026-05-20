"""Unit tests for the RunInstances error taxonomy.

The `classify_run_instances_error` helper maps botocore exception codes
to the design's stable `error_code` taxonomy (`spot-no-capacity`,
`ami-missing`, `quota-exceeded`, `iam-not-ready`, plus the catch-all
`unknown-spawn-failure`). Tests cover one example per stable code so
a future map edit fails fast if it drops a row.
"""

from __future__ import annotations

import pytest

from panakoes_gpu_spawner.aws.ec2 import (
    RUN_INSTANCES_ERROR_MAP,
    RunInstancesFailure,
    classify_run_instances_error,
)


def _client_error(code: str, message: str = "boom") -> Exception:
    """Build a fake ClientError carrying a botocore-shaped `.response` dict."""

    class FakeClientError(Exception):
        def __init__(self, code: str, message: str) -> None:
            super().__init__(message)
            self.response = {"Error": {"Code": code, "Message": message}}

    return FakeClientError(code, message)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("aws_code", "expected_error_code"),
    [
        ("InsufficientInstanceCapacity", "spot-no-capacity"),
        ("MaxSpotInstanceCountExceeded", "spot-no-capacity"),
        ("SpotMaxPriceTooLow", "spot-no-capacity"),
        ("InvalidAMIID.NotFound", "ami-missing"),
        ("InvalidAMIID.Malformed", "ami-missing"),
        ("InvalidAMIID.Unavailable", "ami-missing"),
        ("VcpuLimitExceeded", "quota-exceeded"),
        ("RequestLimitExceeded", "quota-exceeded"),
        ("InstanceLimitExceeded", "quota-exceeded"),
        ("InvalidIamInstanceProfile.NotFound", "iam-not-ready"),
        ("InvalidIamInstanceProfile.Malformed", "iam-not-ready"),
    ],
)
def test_known_error_codes_classify_to_stable_codes(
    aws_code: str,
    expected_error_code: str,
) -> None:
    """Every entry in `RUN_INSTANCES_ERROR_MAP` maps to its stable code."""
    failure = classify_run_instances_error(_client_error(aws_code))
    assert isinstance(failure, RunInstancesFailure)
    assert failure.error_code == expected_error_code
    assert failure.aws_error_code == aws_code


@pytest.mark.unit
def test_unknown_aws_code_collapses_to_unknown_spawn_failure() -> None:
    """An unknown botocore code collapses to `unknown-spawn-failure`."""
    failure = classify_run_instances_error(_client_error("BrandNewErrorCode"))
    assert failure.error_code == "unknown-spawn-failure"
    assert failure.aws_error_code == "BrandNewErrorCode"


@pytest.mark.unit
def test_non_clienterror_exception_classifies_to_unknown() -> None:
    """A non-botocore exception falls through to `unknown-spawn-failure`."""
    failure = classify_run_instances_error(RuntimeError("network blew up"))
    assert failure.error_code == "unknown-spawn-failure"
    assert failure.aws_error_code == ""
    assert "network blew up" in failure.aws_message


@pytest.mark.unit
def test_run_instances_error_map_keys_are_distinct() -> None:
    """No duplicate keys; map values are one of the stable codes."""
    allowed = {"spot-no-capacity", "ami-missing", "quota-exceeded", "iam-not-ready"}
    assert len(RUN_INSTANCES_ERROR_MAP) == len(set(RUN_INSTANCES_ERROR_MAP.keys()))
    assert set(RUN_INSTANCES_ERROR_MAP.values()).issubset(allowed)
