"""`@audit_event` decorator for FastAPI route handlers.

Wrapping a handler with `@audit_event(action=..., source_service=...)`
records a `panakoes_audit.AuditEvent` after the handler returns,
capturing the resolved status code, the request method and path, and
the actor identity attached to `request.state.user` by the upstream
JWT dependency. On an unhandled exception, the same fields are
recorded with `status="failed"` and the exception is re-raised so the
framework's normal error path runs unchanged.

Resource type and resource id are not part of the decorator's surface
because the decorator does not know the handler's domain model. Both
default to derivations from the request: `resource_type` is the
domain prefix of the action (the `<domain>` of `<domain>.<verb>`) and
`resource_id` is the request path. Handlers that need richer values
should call `record_event` directly inside the body.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

from panakoes_audit import record_event
from starlette.requests import Request

from panakoes_middleware.correlation import get_request_id

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


def _find_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Request | None:
    """Return the `Request` argument in a FastAPI handler's call, if any.

    FastAPI injects the request as a typed parameter; it can land in
    either positional or keyword arguments depending on the signature.
    We sweep both and return the first match.
    """
    for value in args:
        if isinstance(value, Request):
            return value
    for value in kwargs.values():
        if isinstance(value, Request):
            return value
    return None


def _resolve_actor(request: Request | None) -> tuple[str, str]:
    """Return `(actor_id, actor_type)` from `request.state.user.sub` if set.

    Anonymous and pre-auth code paths are still observable: when the
    upstream JWT dependency has not populated `state.user`, we record
    `actor_id="anonymous"` and `actor_type="anonymous"` so the audit
    log keeps a row for the call. This avoids the gap that silent
    authentication failures could create in the audit trail.
    """
    if request is None:
        return "anonymous", "anonymous"
    user = getattr(request.state, "user", None)
    if user is None:
        return "anonymous", "anonymous"
    sub = getattr(user, "sub", None)
    if sub is None:
        return "anonymous", "anonymous"
    return str(sub), "user"


def _domain_of(action: str) -> str:
    """Return the `<domain>` segment of a `<domain>.<verb>` action.

    Used as the default `resource_type` when the decorator is asked to
    record an event the caller did not parameterize further. Falls
    back to the entire action string when there is no dot, so the
    audit row still lands.
    """
    if "." in action:
        return action.split(".", 1)[0]
    return action


def _status_code_of(response: Any) -> int:
    """Best-effort extraction of an HTTP status code from a handler return.

    FastAPI handlers return one of several shapes: a `Response`
    instance with a `status_code` attribute, a Pydantic model or dict
    that FastAPI converts to a 200 response, or `None`. We look for
    `status_code` first and otherwise assume 200 (the framework
    default for a successful return).
    """
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return 200


def audit_event(
    *,
    action: str,
    source_service: str,
    actor_type: str = "user",
) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
    """Wrap a FastAPI handler so it emits an audit event after it runs.

    Usage:

        @router.post("/widgets")
        @audit_event(action="widget.created", source_service="widget-api")
        async def create_widget(request: Request) -> Widget:
            ...

    The decorator preserves the wrapped handler's signature (so
    FastAPI dependency injection still works) and emits one event per
    invocation, success or failure.
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, Any]],
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        if not inspect.iscoroutinefunction(func):
            raise TypeError("audit_event can only wrap async handlers")

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request = _find_request(args, kwargs)
            actor_id, resolved_actor_type = _resolve_actor(request)
            actor_type_value = (
                resolved_actor_type if resolved_actor_type == "anonymous" else actor_type
            )
            request_id = get_request_id() or None
            method = request.method if request is not None else ""
            path = request.url.path if request is not None else ""
            try:
                response = await func(*args, **kwargs)
            except Exception:
                await record_event(
                    actor_id=actor_id,
                    actor_type=actor_type_value,
                    action=action,
                    resource_type=_domain_of(action),
                    resource_id=path or _domain_of(action),
                    source_service=source_service,
                    request_id=request_id,
                    details={
                        "path": path,
                        "method": method,
                        "status": "failed",
                    },
                )
                raise
            await record_event(
                actor_id=actor_id,
                actor_type=actor_type_value,
                action=action,
                resource_type=_domain_of(action),
                resource_id=path or _domain_of(action),
                source_service=source_service,
                request_id=request_id,
                details={
                    "path": path,
                    "method": method,
                    "status_code": _status_code_of(response),
                    "status": "success",
                },
            )
            return response

        return wrapper

    return decorator
