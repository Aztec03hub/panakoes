"""Integration tests for `POST /api/v1/transcribe/{ingestion_id}`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import boto3
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from panakoes_transcriber import (
    TranscriberAuthError,
    TranscriptionResult,
    TranscriptionSegment,
    Word,
)

from panakoes_ingestion_api.main import app
from panakoes_ingestion_api.routes.ingestion import (
    get_ingestion_store,
    get_settings,
    get_url_generator,
)
from panakoes_ingestion_api.routes.transcribe import get_transcriber_dep
from panakoes_ingestion_api.storage.dynamodb import IngestionStore
from panakoes_ingestion_api.storage.s3 import S3PresignedUrlGenerator
from tests.conftest import TEST_BUCKET_NAME, TEST_REGION, TEST_TABLE_NAME


class _FakeTranscriber:
    """In-memory `Transcriber` used by every integration test below."""

    def __init__(self, result: TranscriptionResult | Exception) -> None:
        self._result = result
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        language_hint: str | None = None,
    ) -> TranscriptionResult:
        self.calls.append((audio_bytes, filename))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _success_result() -> TranscriptionResult:
    return TranscriptionResult(
        text="hello world",
        segments=(
            TranscriptionSegment(
                text="hello world",
                start=0.0,
                end=1.5,
                words=(
                    Word(text="hello", start=0.0, end=0.5),
                    Word(text="world", start=0.6, end=1.5),
                ),
            ),
        ),
        language="en",
        duration_seconds=1.5,
    )


@pytest_asyncio.fixture
async def transcribe_client(
    dynamodb_table: Any,
    s3_bucket: str,
    test_settings: Any,
) -> AsyncIterator[tuple[AsyncClient, _FakeTranscriber]]:
    """Async httpx client wired with a fake transcriber."""
    store = IngestionStore(table_name=TEST_TABLE_NAME, region_name=TEST_REGION)
    url_generator = S3PresignedUrlGenerator(bucket=TEST_BUCKET_NAME, region_name=TEST_REGION)
    fake = _FakeTranscriber(_success_result())

    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_ingestion_store] = lambda: store
    app.dependency_overrides[get_url_generator] = lambda: url_generator
    app.dependency_overrides[get_transcriber_dep] = lambda: fake

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, fake
    finally:
        app.dependency_overrides.clear()


async def _create_and_upload(
    client: AsyncClient,
    auth_headers: dict[str, str],
    *,
    audio_bytes: bytes = b"riff-bytes",
    filename: str = "demo.m4a",
) -> str:
    """Create an ingestion intent and put the corresponding audio in S3."""
    response = await client.post(
        "/ingestion/audio",
        headers=auth_headers,
        json={
            "filename": filename,
            "content_type": "audio/mp4",
            "size_bytes": len(audio_bytes),
        },
    )
    assert response.status_code == 201
    body = response.json()
    ingestion_id = body["ingestion_id"]

    # Look up the s3_key the API picked, then put the bytes there.
    record_response = await client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    s3_key = record_response.json()["s3_key"]
    boto3.client("s3", region_name=TEST_REGION).put_object(
        Bucket=TEST_BUCKET_NAME, Key=s3_key, Body=audio_bytes
    )
    return ingestion_id


@pytest.mark.integration
async def test_transcribe_route_schedules_and_persists(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
    auth_headers: dict[str, str],
) -> None:
    """Happy path: 202-style return + transcript visible on next GET."""
    client, fake = transcribe_client
    ingestion_id = await _create_and_upload(client, auth_headers)

    response = await client.post(
        f"/api/v1/transcribe/{ingestion_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    # The response includes the (now-pending or already-succeeded) record.
    assert body["ingestion_id"] == ingestion_id
    assert body["transcript_status"] in {"pending", "succeeded"}

    # BackgroundTasks fires before the AsyncClient context exits, so by
    # the time we GET, the transcript should be persisted.
    get_response = await client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    get_body = get_response.json()
    assert get_body["transcript_status"] == "succeeded"
    assert get_body["transcript"]["text"] == "hello world"
    assert get_body["transcript"]["language"] == "en"
    assert len(fake.calls) == 1


@pytest.mark.integration
async def test_transcribe_route_idempotent_when_succeeded(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
    auth_headers: dict[str, str],
) -> None:
    """Calling twice does not re-run the transcriber."""
    client, fake = transcribe_client
    ingestion_id = await _create_and_upload(client, auth_headers)

    await client.post(f"/api/v1/transcribe/{ingestion_id}", headers=auth_headers)
    # Force the background task to settle by issuing a read.
    await client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)

    # Second call should short-circuit: no additional fake invocation.
    response = await client.post(
        f"/api/v1/transcribe/{ingestion_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["transcript_status"] == "succeeded"
    assert len(fake.calls) == 1


@pytest.mark.integration
async def test_transcribe_route_retries_after_failure(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
    auth_headers: dict[str, str],
) -> None:
    """Failed transcripts can be retried; second call re-invokes the backend."""
    client, fake = transcribe_client
    ingestion_id = await _create_and_upload(client, auth_headers)

    # First run: fail.
    fake._result = TranscriberAuthError("bad creds")  # type: ignore[attr-defined]
    await client.post(f"/api/v1/transcribe/{ingestion_id}", headers=auth_headers)
    first_get = await client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    assert first_get.json()["transcript_status"] == "failed"

    # Second run: succeed.
    fake._result = _success_result()  # type: ignore[attr-defined]
    response = await client.post(
        f"/api/v1/transcribe/{ingestion_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    second_get = await client.get(f"/ingestion/{ingestion_id}", headers=auth_headers)
    assert second_get.json()["transcript_status"] == "succeeded"
    # Backend was called twice: once that failed, once that succeeded.
    assert len(fake.calls) == 2


@pytest.mark.integration
async def test_transcribe_route_404_for_unknown_id(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
    auth_headers: dict[str, str],
) -> None:
    """Unknown id => 404 (and the transcriber is never called)."""
    client, fake = transcribe_client
    response = await client.post(
        "/api/v1/transcribe/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert fake.calls == []


@pytest.mark.integration
async def test_transcribe_route_404_for_other_users_record(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
    make_token: Any,
) -> None:
    """User B requesting User A's transcription => 404 (parity with GET)."""
    client, fake = transcribe_client
    headers_a = {"Authorization": f"Bearer {make_token(sub='user_a', email='a@x.test', jti='sa')}"}
    headers_b = {"Authorization": f"Bearer {make_token(sub='user_b', email='b@x.test', jti='sb')}"}

    ingestion_id = await _create_and_upload(client, headers_a)

    response = await client.post(
        f"/api/v1/transcribe/{ingestion_id}",
        headers=headers_b,
    )
    assert response.status_code == 404
    assert fake.calls == []


@pytest.mark.integration
async def test_transcribe_route_requires_auth(
    transcribe_client: tuple[AsyncClient, _FakeTranscriber],
) -> None:
    """No Authorization header => 401."""
    client, _fake = transcribe_client
    response = await client.post("/api/v1/transcribe/anything")
    assert response.status_code == 401
