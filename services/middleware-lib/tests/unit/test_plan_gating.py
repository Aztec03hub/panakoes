"""Unit tests for ``panakoes_middleware.plan_gating``.

Each test wires the dependency into a tiny FastAPI app with a
hand-rolled "JWT verification" dependency that just stuffs an object
onto ``request.state.user``, then asserts the gate's behaviour at the
HTTP layer (status code + JSON body). This mirrors how consuming
services compose the middleware in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from panakoes_middleware import require_plan
from panakoes_middleware.plan_gating import _coerce_plan


@dataclass
class _Principal:
    """Minimal stand-in for a JWT-verified user with a plan claim."""

    user_id: str
    plan: Literal["free", "pro", "team"]


def _build_app(*, required: Literal["free", "pro", "team"], user: Any) -> FastAPI:
    """Build a FastAPI app whose protected route gates on ``required``."""
    app = FastAPI()

    def _attach_user(request: Request) -> None:
        request.state.user = user

    @app.get(
        "/protected",
        dependencies=[Depends(_attach_user), Depends(require_plan(required))],
    )
    def _protected() -> dict[str, str]:
        return {"ok": "yes"}

    return app


@pytest.mark.parametrize(
    ("required", "plan"),
    [
        ("pro", "pro"),
        ("pro", "team"),
        ("team", "team"),
        ("free", "free"),
        ("free", "pro"),
    ],
)
def test_gate_admits_when_plan_meets_threshold(
    required: Literal["free", "pro", "team"],
    plan: Literal["free", "pro", "team"],
) -> None:
    """Plans at or above the threshold pass the gate (200)."""
    app = _build_app(required=required, user=_Principal(user_id="u1", plan=plan))
    client = TestClient(app)
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}


@pytest.mark.parametrize(
    ("required", "plan"),
    [
        ("pro", "free"),
        ("team", "free"),
        ("team", "pro"),
    ],
)
def test_gate_returns_402_when_plan_below_threshold(
    required: Literal["free", "pro", "team"],
    plan: Literal["free", "pro", "team"],
) -> None:
    """Plans below the threshold fail with 402 and a structured detail."""
    app = _build_app(required=required, user=_Principal(user_id="u1", plan=plan))
    client = TestClient(app)
    response = client.get("/protected")
    assert response.status_code == 402
    body = response.json()
    assert body["detail"] == {
        "detail": "plan_required",
        "required_plan": required,
        "current_plan": plan,
    }


def test_gate_treats_unknown_plan_as_free() -> None:
    """A garbage plan claim is coerced to ``free`` rather than admitted."""
    app = _build_app(required="pro", user=_Principal(user_id="u1", plan="platinum"))  # type: ignore[arg-type]
    client = TestClient(app)
    response = client.get("/protected")
    assert response.status_code == 402
    assert response.json()["detail"]["current_plan"] == "free"


def test_gate_accepts_dict_principal() -> None:
    """A mapping-shaped user (e.g. dict from a TS service) still works."""
    app = _build_app(required="pro", user={"user_id": "u1", "plan": "team"})
    client = TestClient(app)
    response = client.get("/protected")
    assert response.status_code == 200


def test_gate_returns_401_when_no_user_attached() -> None:
    """Missing principal raises 401 (auth precondition), not 402."""
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_plan("pro"))])
    def _protected() -> dict[str, str]:
        return {"ok": "yes"}  # pragma: no cover

    client = TestClient(app)
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_coerce_plan_helper() -> None:
    """The internal coercion helper is exercised at the unit level."""
    assert _coerce_plan("free") == "free"
    assert _coerce_plan("pro") == "pro"
    assert _coerce_plan("team") == "team"
    assert _coerce_plan(None) == "free"
    assert _coerce_plan("admin") == "free"
    assert _coerce_plan(42) == "free"
