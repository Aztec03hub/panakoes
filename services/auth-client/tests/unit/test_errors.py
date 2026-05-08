"""Smoke tests for the exception types.

Exception classes have no logic, but ensuring they are subclasses of
`Exception` (and not e.g. `BaseException`) and carry the message
prevents accidental regression if anyone refactors them.
"""

from __future__ import annotations

import pytest

from panakoes_auth_client import JwtConfigError, JwtInvalidError


@pytest.mark.unit
def test_jwt_invalid_error_is_an_exception() -> None:
    """`JwtInvalidError` is a regular `Exception` so `try/except Exception` catches it."""
    assert issubclass(JwtInvalidError, Exception)


@pytest.mark.unit
def test_jwt_config_error_is_an_exception() -> None:
    """`JwtConfigError` is a regular `Exception` for the same reason."""
    assert issubclass(JwtConfigError, Exception)


@pytest.mark.unit
def test_invalid_error_carries_message() -> None:
    """The constructor stores the message for `str()` and logging."""
    err = JwtInvalidError("token expired")

    assert str(err) == "token expired"


@pytest.mark.unit
def test_config_error_carries_message() -> None:
    """Same for the config error."""
    err = JwtConfigError("missing JWT_SECRET")

    assert str(err) == "missing JWT_SECRET"


@pytest.mark.unit
def test_invalid_and_config_errors_are_distinct() -> None:
    """The two error types are not interchangeable."""
    assert not issubclass(JwtInvalidError, JwtConfigError)
    assert not issubclass(JwtConfigError, JwtInvalidError)


@pytest.mark.unit
def test_invalid_error_is_raisable_and_catchable() -> None:
    """Round-trip through `raise / except` to confirm normal exception semantics."""
    with pytest.raises(JwtInvalidError):
        raise JwtInvalidError("x")
