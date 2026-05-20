"""Unit tests for the WebSocket $connect Lambda authorizer.

Covers eight cases (4 success, 4 failure) plus a handful of edge
cases to drive 100% branch coverage on the auth path.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from panakoes_ws_authorizer import build_response, extract_token, lambda_handler

# ---------------------------------------------------------------------------
# Happy-path claims (4 tests)
# ---------------------------------------------------------------------------


def test_valid_token_via_query_string_authorized(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A well-formed token in `?token=` authorizes the connection."""
    token = make_token()
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["user_id"] == "user_abc"


def test_valid_token_via_authorization_header_authorized(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A `Authorization: Bearer <jwt>` header authorizes the connection."""
    token = make_token()
    event = make_event(token=token, token_in="header")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["user_id"] == "user_abc"


def test_valid_token_with_role_and_tenant_exposes_them_in_context(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """Optional `role` + `tenant_id` claims surface in the authorizer context."""
    token = make_token(overrides={"role": "admin", "tenant_id": "tenant_xyz"})
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert result["context"]["user_id"] == "user_abc"
    assert result["context"]["role"] == "admin"
    assert result["context"]["tenant_id"] == "tenant_xyz"


def test_valid_token_without_optional_claims_omits_them_from_context(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """When optional claims are absent the context map omits them entirely."""
    token = make_token()
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"
    assert "role" not in result["context"]
    assert "tenant_id" not in result["context"]


# ---------------------------------------------------------------------------
# Failure modes (4 tests)
# ---------------------------------------------------------------------------


def test_expired_token_rejected(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """An expired token returns `Effect: Deny` IAM policy."""
    past = int(time.time()) - 600
    token = make_token(now=past, ttl_seconds=60)  # iat 10 min ago, exp 9 min ago
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_wrong_signature_rejected(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A token signed with a different secret is rejected."""
    token = make_token(secret="attacker-secret")
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_wrong_audience_rejected(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A token issued for a different audience is rejected."""
    token = make_token(audience="some-other-api")
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_missing_token_rejected(
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """No header AND no query param yields `Effect: Deny` IAM policy."""
    event = make_event(token=None)

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


# ---------------------------------------------------------------------------
# Edge cases for full branch coverage
# ---------------------------------------------------------------------------


def test_malformed_authorization_header_rejected(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """`Authorization: <jwt>` (no Bearer prefix) is rejected as malformed."""
    token = make_token()
    event = make_event(token=token, token_in="header-bad")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_garbage_token_rejected(
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A non-JWT string in the query param is rejected."""
    event = make_event(token="not-a-jwt", token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_wrong_issuer_rejected(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """A token from a different issuer is rejected."""
    token = make_token(issuer="https://attacker.example.com")
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_authorization_header_takes_precedence_over_query(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """Header beats query when both are present, even if header is bad."""
    good = make_token()
    bad = "not-a-jwt"
    event = {
        "headers": {"Authorization": f"Bearer {bad}"},
        "queryStringParameters": {"token": good},
        "requestContext": {"routeKey": "$connect"},
    }

    result = lambda_handler(event, None)

    # Header path is bad, so reject; we do not silently fall back to query.
    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


def test_empty_query_string_parameters_handled(
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """`queryStringParameters: None` should not crash extract_token."""
    event = {
        "headers": {},
        "queryStringParameters": None,
        "requestContext": {"routeKey": "$connect"},
    }

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_extract_token_returns_none_when_both_missing() -> None:
    assert extract_token({"headers": {}, "queryStringParameters": {}}) is None


def test_extract_token_strips_bearer_prefix() -> None:
    event = {"headers": {"Authorization": "Bearer xyz"}, "queryStringParameters": {}}
    assert extract_token(event) == "xyz"


def test_extract_token_case_insensitive_header() -> None:
    event = {"headers": {"authorization": "Bearer xyz"}, "queryStringParameters": {}}
    assert extract_token(event) == "xyz"


def test_build_response_minimal() -> None:
    response = build_response(authorized=False, context={})
    assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert response["policyDocument"]["Statement"][0]["Action"] == "execute-api:Invoke"
    assert "context" not in response


def test_build_response_with_context() -> None:
    response = build_response(
        authorized=True,
        context={"user_id": "u1"},
        method_arn="arn:aws:execute-api:us-east-1:1:abc/dev/$connect",
        principal_id="u1",
    )
    assert response["principalId"] == "u1"
    assert response["context"] == {"user_id": "u1"}
    stmt = response["policyDocument"]["Statement"][0]
    assert stmt["Effect"] == "Allow"
    assert stmt["Resource"] == "arn:aws:execute-api:us-east-1:1:abc/dev/$connect"


def test_build_context_handles_unverified_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the unverified re-decode raises, we still return a minimal context."""
    from panakoes_ws_authorizer import authorizer as _mod

    def _raise(*_args: object, **_kwargs: object) -> Any:
        raise ValueError("simulated decode failure")

    monkeypatch.setattr(_mod.pyjwt, "decode", _raise)
    context = _mod._build_context(token="x", validated_sub="u1", validated_role=None)

    assert context == {"user_id": "u1"}


def test_validator_cached_across_invocations(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
) -> None:
    """The module-level validator is reused across invocations."""
    from panakoes_ws_authorizer import authorizer as _mod

    token = make_token()
    event = make_event(token=token, token_in="query")

    lambda_handler(event, None)
    first = _mod._VALIDATOR
    assert first is not None

    lambda_handler(event, None)
    second = _mod._VALIDATOR
    assert second is first


def test_handler_missing_jwt_env_returns_unauthorized(
    make_token: Callable[..., str],
    make_event: Callable[..., dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A boot-time config error must surface as a 401, never a 500.

    Lambda authorizer infra cannot afford a 500: API Gateway turns
    that into a `Internal Server Error` and the connection still
    fails, but the client gets a confusing surface. We swallow the
    `JwtConfigError` and emit `Effect: Deny` IAM policy instead.
    """
    monkeypatch.delenv("JWT_SECRET", raising=False)
    token = make_token()
    event = make_event(token=token, token_in="query")

    result = lambda_handler(event, None)

    assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
    assert "context" not in result
