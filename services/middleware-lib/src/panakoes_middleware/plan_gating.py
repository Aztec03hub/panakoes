"""Plan-gating FastAPI dependency.

`require_plan("pro")` returns a FastAPI dependency that resolves to
the authenticated principal's plan claim and raises HTTP 402 Payment
Required when the claim is below the requested threshold. Any service
that wants to gate a route on a paid tier wires the dependency in,
which keeps the contract in one place across Panakoes.

Contract (mirrored in `services/billing/README.md`):

- The JWT carries a ``plan`` claim with one of ``free`` | ``pro`` |
  ``team``. Missing or unknown values are treated as ``free``.
- Rank: ``free < pro < team``. ``require_plan("pro")`` admits ``pro``
  and ``team``; ``require_plan("team")`` admits ``team`` only.
- Insufficient plan returns HTTP 402 with a stable JSON body
  ``{"detail": "plan_required", "required_plan": "<requested>",
  "current_plan": "<claim>"}``. 402 is the appropriate status for
  "the request was understood, the principal is authenticated, but
  payment is required to access this resource".
- A request that arrives without a verified JWT (no ``request.state``
  user attached) raises 401, not 402, because authentication is the
  precondition for plan-gating to mean anything.

Consuming services attach the verified JWT principal to
``request.state.user`` in their JWT verification dependency (see
``panakoes_auth_client``). The plan-gating dependency reads
``request.state.user.plan`` and compares against the threshold.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request, status

Plan = Literal["free", "pro", "team"]
"""The three plan values the gate recognises."""

_PLAN_RANK: dict[str, int] = {"free": 0, "pro": 1, "team": 2}


def _coerce_plan(value: object) -> Plan:
    """Coerce an arbitrary JWT claim value into a known plan.

    Unknown / missing values map to ``free``: a malformed claim must
    never accidentally upgrade the caller. This is the conservative
    default the security review insisted on.
    """
    if value == "team":
        return "team"
    if value == "pro":
        return "pro"
    return "free"


def _extract_plan_from_request(request: Request) -> Plan:
    """Pull the plan claim from ``request.state.user``.

    The user object is attached by the upstream JWT verification
    dependency. Both attribute-style access (a dataclass principal)
    and mapping-style access (a plain dict) are supported so this
    dependency works across the polyglot principal shapes Panakoes
    services use.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if isinstance(user, dict):
        return _coerce_plan(user.get("plan"))
    return _coerce_plan(getattr(user, "plan", None))


def require_plan(required: Plan) -> object:
    """Return a FastAPI dependency that enforces a minimum plan.

    Usage::

        @router.post(
            "/v1/transcribe/deep-summary",
            dependencies=[Depends(require_plan("pro"))],
        )
        async def deep_summary(...): ...

    The dependency raises 402 when the JWT's ``plan`` claim ranks
    below ``required``. It returns the resolved plan on success so a
    handler that wants to render a tier-aware response can also write
    ``plan: Annotated[Plan, Depends(require_plan("pro"))]``.
    """
    if required not in _PLAN_RANK:
        raise ValueError(f"unknown plan: {required!r}")  # pragma: no cover

    threshold = _PLAN_RANK[required]

    def _dep(request: Request) -> Plan:
        current = _extract_plan_from_request(request)
        if _PLAN_RANK[current] < threshold:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "detail": "plan_required",
                    "required_plan": required,
                    "current_plan": current,
                },
            )
        return current

    return _dep
