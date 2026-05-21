"""Unit tests for the transcriber-stream main module's status emits.

The integration test under `tests/integration/test_main.py` exercises
the full `run()` orchestration end-to-end against moto. This file
covers narrow, fast assertions on the new real-time observability
status envelopes the container emits at every phase of startup
(container-started, cuda-checked, model-loading, model-loaded,
prompt-seed-read, warmup-complete) plus the discipline that an emit
failure never propagates.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from panakoes_transcriber_stream.main import _emit_status, _log_cuda_environment

# ---------------------------------------------------------------------------
# _emit_status: shape + best-effort behavior
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_sends_status_envelope_via_ws_publisher() -> None:
    """A successful emit calls `ws.send` with the canonical status shape."""
    ws = MagicMock()
    ws.send = AsyncMock(return_value=True)

    await _emit_status(
        ws,
        stage="model-loading",
        detail="Loading Whisper model from baked AMI",
    )

    ws.send.assert_awaited_once()
    payload = ws.send.await_args.args[0]
    assert payload["type"] == "status"
    assert payload["stage"] == "model-loading"
    assert payload["detail"] == "Loading Whisper model from baked AMI"
    assert "ts" in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_threads_extra_fields_through() -> None:
    """Caller-supplied extras (model_size, elapsed_s, connection_id) land
    on the envelope verbatim."""
    ws = MagicMock()
    ws.send = AsyncMock(return_value=True)

    await _emit_status(
        ws,
        stage="container-started",
        detail="Transcriber container started (large-v2)",
        extra={"model_size": "large-v2", "connection_id": "conn-abc"},
    )

    payload = ws.send.await_args.args[0]
    assert payload["model_size"] == "large-v2"
    assert payload["connection_id"] == "conn-abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_extras_cannot_overwrite_reserved_keys() -> None:
    """A buggy `extra` dict cannot rewrite `type`, `stage`, `detail`, `ts`."""
    ws = MagicMock()
    ws.send = AsyncMock(return_value=True)

    await _emit_status(
        ws,
        stage="real",
        detail="d",
        extra={"type": "ATTACK", "stage": "fake", "ts": "evil", "ok": True},
    )

    payload = ws.send.await_args.args[0]
    assert payload["type"] == "status"
    assert payload["stage"] == "real"
    assert payload["detail"] == "d"
    assert payload["ts"] != "evil"
    assert payload["ok"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_swallows_ws_send_failure() -> None:
    """`ws.send` raising must NOT propagate; status emits are best-effort."""
    ws = MagicMock()
    ws.send = AsyncMock(side_effect=RuntimeError("ws down"))

    # No raise expected; logged + suppressed inside _emit_status.
    await _emit_status(ws, stage="model-loading", detail="d")
    ws.send.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_works_when_send_returns_false() -> None:
    """`ws.send` returning False (publisher is gone) is a normal path; the
    helper must not treat it as an error."""
    ws = MagicMock()
    ws.send = AsyncMock(return_value=False)
    await _emit_status(ws, stage="model-loading", detail="d")
    ws.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# _log_cuda_environment: returns a structured summary the emit consumes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_log_cuda_environment_returns_summary_with_required_keys(tmp_path: Any) -> None:
    """The function returns a dict carrying the cuda summary the SPA
    surfaces in the `cuda-checked` status envelope.

    The actual `torch` import is best-effort under the test runner
    (no GPU) but the function always returns the canonical shape.
    """
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    summary = _log_cuda_environment(str(model_dir))
    assert "cuda_available" in summary
    assert "device_count" in summary
    assert "device_name" in summary


@pytest.mark.unit
def test_log_cuda_environment_returns_summary_for_missing_dir(tmp_path: Any) -> None:
    """A missing model_dir is logged but does not break the summary."""
    summary = _log_cuda_environment(str(tmp_path / "absent"))
    assert isinstance(summary, dict)
    assert "cuda_available" in summary


# ---------------------------------------------------------------------------
# Envelope is JSON-serializable so it survives a WsPublisher post path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_emit_status_envelope_is_json_serializable() -> None:
    """The envelope produced by _emit_status must be JSON-serializable so
    the WsPublisher's `post_to_connection(Data=json.dumps(payload))` path
    does not throw on encoding."""
    ws = MagicMock()
    captured: list[dict[str, Any]] = []

    async def fake_send(payload: dict[str, Any]) -> bool:
        captured.append(payload)
        return True

    ws.send = fake_send

    await _emit_status(
        ws,
        stage="instance-launching",
        detail="Instance i-deadbeef launching",
        extra={"instance_id": "i-deadbeef"},
    )

    assert len(captured) == 1
    raw = json.dumps(captured[0])
    parsed = json.loads(raw)
    assert parsed["type"] == "status"
    assert parsed["instance_id"] == "i-deadbeef"
