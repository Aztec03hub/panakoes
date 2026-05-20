"""Tests for the S3 + DDB persistence facade (moto)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from panakoes_transcriber_stream.persistence import (
    Persistence,
    read_prompt_seed_from_ddb,
)


@dataclass
class _Token:
    text: str
    start: float
    end: float
    probability: float | None = 0.9


def test_read_prompt_seed_returns_seed_when_present(ddb_resource: Any, sessions_table: Any) -> None:
    sessions_table.update_item(
        Key={"session_id": "sess_test1234567890ab"},
        UpdateExpression="SET prompt_seed_text = :v",
        ExpressionAttributeValues={":v": "  carryover prompt  "},
    )
    seed = read_prompt_seed_from_ddb(
        "sess_test1234567890ab",
        sessions_table="panakoes-dev-streaming-sessions",
        ddb_resource=ddb_resource,
    )
    assert seed == "carryover prompt"


def test_read_prompt_seed_absent_returns_none(ddb_resource: Any, sessions_table: Any) -> None:
    seed = read_prompt_seed_from_ddb(
        "sess_test1234567890ab",
        sessions_table="panakoes-dev-streaming-sessions",
        ddb_resource=ddb_resource,
    )
    assert seed is None


def test_read_prompt_seed_missing_row_returns_none(ddb_resource: Any, sessions_table: Any) -> None:
    seed = read_prompt_seed_from_ddb(
        "sess_does_not_exist",
        sessions_table="panakoes-dev-streaming-sessions",
        ddb_resource=ddb_resource,
    )
    assert seed is None


def test_read_prompt_seed_swallows_ddb_errors() -> None:
    class _BoomResource:
        def Table(self, name: str) -> Any:
            raise RuntimeError("ddb down")

    seed = read_prompt_seed_from_ddb("sess_test", sessions_table="t", ddb_resource=_BoomResource())
    assert seed is None


@pytest.mark.asyncio
async def test_update_last_transcript_tokens_concatenates(
    s3_client: Any,
    ddb_resource: Any,
    sessions_table: Any,
) -> None:
    persist = Persistence(
        "panakoes-dev-transcripts",
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        ddb_resource=ddb_resource,
    )
    await persist.update_last_transcript_tokens(
        [_Token(text=" hello", start=0.0, end=0.5), _Token(text=" world", start=0.6, end=1.0)],
        audio_upto=1.0,
    )
    await persist.update_last_transcript_tokens(
        [_Token(text=" again", start=1.1, end=1.5)], audio_upto=1.5
    )
    item = sessions_table.get_item(Key={"session_id": "sess_test1234567890ab"})["Item"]
    assert item["last_transcript_text"] == " hello world again"
    assert float(item["last_processed_upto"]) == 1.5


@pytest.mark.asyncio
async def test_update_last_transcript_tokens_empty_is_noop(
    ddb_resource: Any, sessions_table: Any
) -> None:
    persist = Persistence(
        "panakoes-dev-transcripts",
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        ddb_resource=ddb_resource,
    )
    await persist.update_last_transcript_tokens([], audio_upto=0.0)
    item = sessions_table.get_item(Key={"session_id": "sess_test1234567890ab"})["Item"]
    assert "last_transcript_text" not in item


@pytest.mark.asyncio
async def test_write_final_tokens_writes_s3_and_flips_status(
    s3_client: Any, ddb_resource: Any, sessions_table: Any
) -> None:
    persist = Persistence(
        "panakoes-dev-transcripts",
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        s3_client=s3_client,
        ddb_resource=ddb_resource,
    )
    tokens = [_Token(text=" final word", start=2.0, end=2.5)]
    key = await persist.write_final_tokens(tokens, audio_upto=2.5, committed=True)
    assert key == "streaming/sess_test1234567890ab/transcript.json"

    obj = s3_client.get_object(Bucket="panakoes-dev-transcripts", Key=key)
    body = json.loads(obj["Body"].read())
    assert body["session_id"] == "sess_test1234567890ab"
    assert body["committed_finalized"] is True
    assert body["tokens"][0]["text"] == " final word"

    item = sessions_table.get_item(Key={"session_id": "sess_test1234567890ab"})["Item"]
    assert item["status"] == "ended"
    assert item["final_transcript_s3_key"] == key


@pytest.mark.asyncio
async def test_write_final_tokens_custom_status(
    s3_client: Any, ddb_resource: Any, sessions_table: Any
) -> None:
    persist = Persistence(
        "panakoes-dev-transcripts",
        "panakoes-dev-streaming-sessions",
        "sess_test1234567890ab",
        s3_client=s3_client,
        ddb_resource=ddb_resource,
    )
    await persist.write_final_tokens(
        [_Token(text=" x", start=0.0, end=0.5)],
        audio_upto=0.5,
        committed=True,
        status="interrupted",
    )
    item = sessions_table.get_item(Key={"session_id": "sess_test1234567890ab"})["Item"]
    assert item["status"] == "interrupted"
