"""Shared dependency factories for admin-api routes.

Each function returns a long-lived per-request handle (DynamoDB
table resource, lifecycle-state store) from `app.state` populated
by the lifespan. Tests override these dependencies with moto-backed
equivalents via `app.dependency_overrides[...]`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

from panakoes_admin_api.lifecycle_state import LifecycleStateStore

if TYPE_CHECKING:
    pass


def get_audit_table(request: Request) -> Any:
    """Return the request-scoped audit-log DynamoDB table from app state."""
    return request.app.state.audit_table


def get_lifecycle_state(request: Request) -> LifecycleStateStore:
    """Return the request-scoped lifecycle-state store from app state."""
    state: LifecycleStateStore = request.app.state.lifecycle_state
    return state


def get_streaming_sessions_table(request: Request) -> Any:
    """Return the request-scoped streaming-sessions DynamoDB table from app state."""
    return request.app.state.streaming_sessions_table


def get_ingestion_table(request: Request) -> Any:
    """Return the request-scoped ingestion DynamoDB table from app state."""
    return request.app.state.ingestion_table


def get_tenants_table(request: Request) -> Any:
    """Return the request-scoped tenants DynamoDB table from app state."""
    return request.app.state.tenants_table


def get_api_keys_table(request: Request) -> Any:
    """Return the request-scoped api-keys DynamoDB table from app state."""
    return request.app.state.api_keys_table


def get_eventbridge_publisher(request: Request) -> Any:
    """Return the request-scoped EventBridge publisher from app state."""
    return request.app.state.eventbridge_publisher


def get_batch_client(request: Request) -> Any:
    """Return the request-scoped AWS Batch client wrapper from app state."""
    return request.app.state.batch_client
