"""Integration test for ``main.run`` end-to-end with mocked GPU.

The vendored ``backend_factory`` is replaced with a stub that returns a
fake ``asr`` (no GPU, no model load, no Whisper). The SQS consumer
reads real moto messages; the DDB row updates land in moto; the S3
final transcript lands in moto. The point is to prove the orchestration
shape works without needing a real GPU.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from panakoes_transcriber_stream import main as main_mod
from panakoes_transcriber_stream.config import load_config_from_env


@dataclass
class _StubToken:
    text: str
    start: float
    end: float
    probability: float | None = 0.95
    speaker: int = -1
    detected_language: str | None = None


class _StubAsr:
    """Stand-in for the vendored FasterWhisperASR object.

    The wrapper code only ever touches the attributes the vendored
    ``OnlineASRProcessor`` sets after the factory call.
    """

    sep = " "
    tokenizer = None
    confidence_validation = False
    buffer_trimming = "segment"
    buffer_trimming_sec = 15.0
    backend_choice = "stub"


@pytest.fixture
def ami_assets(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """Lay down the AMI-baked assets the startup assertion checks for."""

    model_root = tmp_path / "whisper" / "models"
    model_dir = model_root / "large-v2-ct2"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"\x00")

    warmup = tmp_path / "whisper" / "warmup-1s.wav"
    warmup.write_bytes(b"RIFF....")

    monkeypatch.setenv("MODEL_CACHE_DIR", str(model_root))
    # Override the warmup path to live under tmp_path; the Config dataclass
    # hardcodes ``/opt/whisper/warmup-1s.wav`` so we monkey-patch the
    # assertion to look at the tmp path instead.
    monkeypatch.setattr(
        "panakoes_transcriber_stream.config.Config.warmup_clip_path",
        property(lambda _self: str(warmup)),
    )
    return str(warmup)


def _push_frame(sqs_client: Any, queue_url: str, *, samples: int = 3200) -> None:
    pcm = b"\x00\x01" * samples
    envelope = {
        "action": "audio-frame",
        "v": 1,
        "seq": 1,
        "ts_ms_delta": 0,
        "pcm_b64": base64.b64encode(pcm).decode("ascii"),
    }
    sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(envelope))


@pytest.mark.asyncio
async def test_run_happy_path_normal_disconnect(
    valid_env: dict[str, str],
    ami_assets: str,
    s3_client: Any,
    sqs_client: Any,
    sqs_queue: str,
    ddb_resource: Any,
    sessions_table: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Point the config at the live moto queue.
    monkeypatch.setenv("FRAME_QUEUE_URL", sqs_queue)
    cfg = load_config_from_env()

    # Shorten the lifecycle watcher's DDB poll interval so the test
    # observes the disconnect within the test's timeout window.
    real_init = "panakoes_transcriber_stream.lifecycle.LifecycleWatcher.__init__"

    def _fast_lifecycle_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs["poll_interval_seconds"] = 0.05
        from panakoes_transcriber_stream.lifecycle import LifecycleWatcher

        # Call the original __init__ via super() bypass.
        LifecycleWatcher.__orig_init__(self, *args, **kwargs)  # type: ignore[attr-defined]

    from panakoes_transcriber_stream.lifecycle import LifecycleWatcher as _LW

    if not hasattr(_LW, "__orig_init__"):
        _LW.__orig_init__ = _LW.__init__  # type: ignore[attr-defined]
    monkeypatch.setattr(real_init, _fast_lifecycle_init)

    # Short-poll the SQS consumer so the test does not wait 20 s for an
    # empty queue. The container's default WaitTimeSeconds=20 is correct
    # for production cold-path budgets but slows this test loop down.
    from panakoes_transcriber_stream.sqs_consumer import SQSConsumer as _SQS

    if not hasattr(_SQS, "__orig_init__"):
        _SQS.__orig_init__ = _SQS.__init__  # type: ignore[attr-defined]

    def _fast_sqs_init(self: Any, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("wait_time_seconds", 0)
        _SQS.__orig_init__(self, *args, **kwargs)  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "panakoes_transcriber_stream.sqs_consumer.SQSConsumer.__init__",
        _fast_sqs_init,
    )

    # Inject a few frames into the queue.
    _push_frame(sqs_client, sqs_queue)
    _push_frame(sqs_client, sqs_queue)

    # WS publisher: mock-out boto3 so PostToConnection is a no-op.
    ws_client = MagicMock()

    # Factory stub: returns the StubAsr; the wrapper builds an
    # OnlineASRProcessor around it which actually runs (no GPU; the asr
    # never gets called because we ALSO monkey-patch process_iter and
    # finish on the SeededOnlineASRProcessor below).
    factory_calls: list[dict[str, Any]] = []

    def fake_factory(**kwargs: Any) -> Any:
        factory_calls.append(kwargs)
        return _StubAsr()

    # Replace process_iter + finish so we don't actually call any vendored
    # ASR transcribe path. process_iter returns one committed token per
    # call; finish returns no remainder.
    call_state: dict[str, int] = {"iters": 0}

    def fake_process_iter(self: Any) -> tuple[list[_StubToken], float]:
        call_state["iters"] += 1
        if call_state["iters"] == 1:
            return ([_StubToken(text=" hello", start=0.0, end=0.5)], 0.2)
        # Flip the session row to disconnected so the lifecycle watcher
        # observes the exit signal on its next poll.
        sessions_table.update_item(
            Key={"session_id": "sess_test1234567890ab"},
            UpdateExpression="SET #s = :v",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":v": "disconnected"},
        )
        return ([], 0.4)

    def fake_finish(self: Any) -> tuple[list[_StubToken], float]:
        return ([_StubToken(text=" world", start=0.6, end=1.0)], 1.0)

    monkeypatch.setattr(
        "panakoes_transcriber_stream.asr_proxy.SeededOnlineASRProcessor.process_iter",
        fake_process_iter,
    )
    monkeypatch.setattr(
        "panakoes_transcriber_stream.asr_proxy.SeededOnlineASRProcessor.finish",
        fake_finish,
    )

    # Drive the run.
    rc = await asyncio.wait_for(
        main_mod.run(
            cfg,
            factory=fake_factory,
            s3_client=s3_client,
            ddb_resource=ddb_resource,
            sqs_client=sqs_client,
            ws_client=ws_client,
        ),
        timeout=15.0,
    )
    assert rc == 0
    assert len(factory_calls) == 1, "backend_factory must be invoked exactly once"

    # WS should have seen: ready, final (token), maybe final-chunk, ended.
    sent_payloads = [
        json.loads(call.kwargs["Data"].decode("utf-8"))
        for call in ws_client.post_to_connection.call_args_list
    ]
    types = [p["type"] for p in sent_payloads]
    assert "ready" in types
    assert "ended" in types

    # DDB session row was flipped to ended (overwriting the lifecycle's
    # "disconnected" status from the test driver).
    item = sessions_table.get_item(Key={"session_id": "sess_test1234567890ab"})["Item"]
    assert item["status"] == "ended"
    assert item.get("final_transcript_s3_key", "").startswith("streaming/")

    # S3 final transcript exists with the remainder token text.
    obj = s3_client.get_object(
        Bucket="panakoes-dev-transcripts", Key=item["final_transcript_s3_key"]
    )
    body = json.loads(obj["Body"].read())
    assert body["session_id"] == "sess_test1234567890ab"
    assert any(t["text"] == " world" for t in body["tokens"])


@pytest.mark.asyncio
async def test_run_fails_fast_on_missing_ami_assets(
    valid_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Do NOT lay down the AMI assets; main must raise + ws-error.
    monkeypatch.setenv("MODEL_CACHE_DIR", "/this/path/does/not/exist")
    monkeypatch.setattr(
        "panakoes_transcriber_stream.config.Config.warmup_clip_path",
        property(lambda _self: "/also/does/not/exist.wav"),
    )
    cfg = load_config_from_env()

    ws_client = MagicMock()
    with pytest.raises(RuntimeError) as excinfo:
        await asyncio.wait_for(main_mod.run(cfg, ws_client=ws_client), timeout=5.0)
    assert "AMI missing expected asset" in str(excinfo.value)

    # WS got an ami-asset-missing structured error.
    posted = ws_client.post_to_connection.call_args_list
    assert posted, "ws.send must have been attempted with the structured error"
    body = json.loads(posted[0].kwargs["Data"].decode("utf-8"))
    assert body["type"] == "error"
    assert body["code"] == "ami-asset-missing"
