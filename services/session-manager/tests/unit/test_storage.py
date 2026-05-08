"""Unit tests for `panakoes_session_manager.storage.dynamodb` error paths.

Most happy-path coverage of the store comes through the integration
tests (which use moto). These unit tests exercise the error branches
that the integration tests do not naturally hit.
"""

from __future__ import annotations

import pytest

from panakoes_session_manager.storage.dynamodb import SessionStore


@pytest.mark.unit
def test_update_rejects_empty_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `updates` dict raises `ValueError` before any DDB call."""

    class _StubResource:
        def Table(self, _name: str) -> object:
            class _Table:
                def get_item(self, **_kw: object) -> dict[str, object]:
                    return {"Item": {"session_id": "s", "user_id": "u"}}

            return _Table()

    store = SessionStore(
        table_name="t",
        region_name="us-east-1",
        resource=_StubResource(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="must not be empty"):
        store.update(user_id="u", session_id="s", updates={})


@pytest.mark.unit
def test_table_name_property_exposes_constructor_arg() -> None:
    """The `table_name` property reflects what was passed in."""

    class _StubResource:
        def Table(self, _name: str) -> object:
            return object()

    store = SessionStore(
        table_name="my-table",
        region_name="us-east-1",
        resource=_StubResource(),  # type: ignore[arg-type]
    )
    assert store.table_name == "my-table"
