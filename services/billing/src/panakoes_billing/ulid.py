"""Minimal ULID generator for billing event sort keys.

A ULID is a 128-bit identifier that sorts lexicographically by time of
creation. We need only a tiny subset of the spec: monotonic-ish across
a single process is fine for a sort key, and we Crockford base-32
encode the result so it stays URL-safe and readable in DDB queries.

We deliberately avoid pulling in a third-party ULID library. The full
RFC implementation is overkill for one sort key, and a public-repo
service should not chase trivial dependencies.
"""

from __future__ import annotations

import os
import time

# Crockford base-32 alphabet (no I, L, O, U to avoid ambiguity).
_CROCKFORD: str = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_b32(value: int, length: int) -> str:
    """Encode `value` as a Crockford base-32 string, left-padded to `length`."""
    chars: list[str] = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """Return a fresh 26-character ULID-shaped string.

    The first 10 chars are a 48-bit millisecond timestamp; the last 16
    are 80 bits of randomness from `os.urandom`. We do NOT enforce
    monotonicity within the same millisecond beyond what `os.urandom`
    gives us, which is plenty for billing event ordering at human
    scale.
    """
    timestamp_ms = int(time.time() * 1000)
    random_bits = int.from_bytes(os.urandom(10), byteorder="big")
    return _encode_b32(timestamp_ms, 10) + _encode_b32(random_bits, 16)
