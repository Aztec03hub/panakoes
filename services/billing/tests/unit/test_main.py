"""Unit tests for the `panakoes_billing.main` module entry point."""

from __future__ import annotations

from typing import Any

import pytest

from panakoes_billing import main as main_module


@pytest.mark.unit
def test_app_includes_health_and_billing_routes() -> None:
    """The FastAPI app exposes the documented routes."""
    paths = {route.path for route in main_module.app.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert "/billing/checkout-session" in paths
    assert "/billing/portal-session" in paths
    assert "/billing/webhook" in paths
    assert "/billing/subscription" in paths


@pytest.mark.unit
def test_main_invokes_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main()` calls `uvicorn.run` with the documented module path."""
    captured: dict[str, Any] = {}

    def _fake_run(target: str, **kwargs: Any) -> None:
        captured["target"] = target
        captured.update(kwargs)

    monkeypatch.setattr(main_module.uvicorn, "run", _fake_run)
    main_module.main()
    assert captured["target"] == "panakoes_billing.main:app"
    assert captured["host"] == "0.0.0.0"  # noqa: S104  # bind on all interfaces
    assert captured["port"] == 8000
