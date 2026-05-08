"""Sanity tests on the package's public surface."""

from __future__ import annotations

import pytest

import panakoes_models


@pytest.mark.unit
def test_public_api_exports_every_name() -> None:
    """Every name in `__all__` is importable from the package."""
    for name in panakoes_models.__all__:
        assert hasattr(panakoes_models, name), f"missing public export: {name}"


@pytest.mark.unit
def test_public_api_includes_canonical_models() -> None:
    """The canonical domain models are reachable from the top-level import."""
    expected = {
        "User",
        "IngestionRecord",
        "Transcript",
        "TranscriptSegment",
        "Summary",
        "StreamingSession",
        "NotificationRecord",
        "Subscription",
        "ApiError",
    }
    assert expected.issubset(set(panakoes_models.__all__))
