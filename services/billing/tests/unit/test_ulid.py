"""Unit tests for the lightweight ULID generator."""

from __future__ import annotations

import re

import pytest

from panakoes_billing.ulid import _CROCKFORD, _encode_b32, new_ulid


@pytest.mark.unit
def test_new_ulid_is_26_chars_crockford_only() -> None:
    """Generated ULIDs are 26 chars long and use only the Crockford alphabet."""
    value = new_ulid()
    assert len(value) == 26
    pattern = re.compile(rf"^[{_CROCKFORD}]{{26}}$")
    assert pattern.match(value)


@pytest.mark.unit
def test_new_ulid_values_are_unique() -> None:
    """Many generated ULIDs do not collide (basic randomness sanity)."""
    values = {new_ulid() for _ in range(2000)}
    assert len(values) == 2000


@pytest.mark.unit
def test_new_ulids_are_time_ordered() -> None:
    """Two ULIDs generated in sequence sort lexicographically by creation order.

    The timestamp prefix makes this deterministic at sleep granularity.
    Same-millisecond pairs may tie on prefix and resolve via the random
    suffix, which is why we sleep between generations.
    """
    import time

    first = new_ulid()
    time.sleep(0.005)
    second = new_ulid()
    assert first < second


@pytest.mark.unit
def test_encode_b32_pads_left() -> None:
    """`_encode_b32(0, 5)` is five zero characters."""
    assert _encode_b32(0, 5) == "00000"


@pytest.mark.unit
def test_encode_b32_round_trip_small_values() -> None:
    """Small values encode to the expected Crockford characters."""
    # 1 -> last char "1", everything else padded with "0".
    assert _encode_b32(1, 4) == "0001"
    # 31 -> "Z" (the last char in the Crockford alphabet).
    assert _encode_b32(31, 1) == "Z"
    # 32 -> "10" (one position carry).
    assert _encode_b32(32, 2) == "10"
