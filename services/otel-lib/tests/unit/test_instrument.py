"""Tests for `instrument_fastapi`, `instrument_boto3`, and `instrument_httpx`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

import panakoes_otel


@pytest.mark.unit
def test_instrument_fastapi_marks_app_instrumented() -> None:
    """The FastAPI app is flagged as OTel-instrumented after the call.

    Different versions of `FastAPIInstrumentor` register either an ASGI
    middleware in `user_middleware` (older releases) or hook into the
    middleware stack at build time (current releases). The contract
    that survives across versions is the `_is_instrumented_by_opentelemetry`
    attribute the instrumentor stamps on the app.
    """
    panakoes_otel.configure(service_name="ingestion-api")
    app = FastAPI()
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is False
    panakoes_otel.instrument_fastapi(app)
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True


@pytest.mark.unit
def test_instrument_boto3_marks_botocore_instrumented() -> None:
    """Calling `instrument_boto3` flips the BotocoreInstrumentor singleton on."""
    panakoes_otel.configure(service_name="ingestion-api")
    try:
        panakoes_otel.instrument_boto3()
        assert BotocoreInstrumentor().is_instrumented_by_opentelemetry is True
    finally:
        BotocoreInstrumentor().uninstrument()


@pytest.mark.unit
def test_instrument_boto3_idempotent() -> None:
    """Calling `instrument_boto3` twice does not raise."""
    panakoes_otel.configure(service_name="ingestion-api")
    try:
        panakoes_otel.instrument_boto3()
        panakoes_otel.instrument_boto3()
        assert BotocoreInstrumentor().is_instrumented_by_opentelemetry is True
    finally:
        BotocoreInstrumentor().uninstrument()


@pytest.mark.unit
def test_instrument_httpx_marks_httpx_instrumented() -> None:
    """Calling `instrument_httpx` flips the HTTPX instrumentor singleton on."""
    panakoes_otel.configure(service_name="ingestion-api")
    try:
        panakoes_otel.instrument_httpx()
        assert HTTPXClientInstrumentor().is_instrumented_by_opentelemetry is True
    finally:
        HTTPXClientInstrumentor().uninstrument()


@pytest.mark.unit
def test_instrument_httpx_idempotent() -> None:
    """Calling `instrument_httpx` twice does not raise."""
    panakoes_otel.configure(service_name="ingestion-api")
    try:
        panakoes_otel.instrument_httpx()
        panakoes_otel.instrument_httpx()
        assert HTTPXClientInstrumentor().is_instrumented_by_opentelemetry is True
    finally:
        HTTPXClientInstrumentor().uninstrument()
