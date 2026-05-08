"""Per-library instrumentation entrypoints.

Each function thinly wraps the corresponding `opentelemetry.instrumentation.*`
package's `Instrumentor`. Wrapping centralizes our service-side
configuration: if we ever want to (for example) enable request body
capture for FastAPI globally, the change lands in one place.

The instrumentor packages are imported lazily inside each function so
services that never call `instrument_boto3()` or `instrument_httpx()`
do not need `botocore` or `httpx` installed at runtime. (This matches
how OpenTelemetry's own contrib packages are structured.)

`Instrumentor.instrument()` raises if called twice without a matching
`uninstrument()`. We guard via `is_instrumented_by_opentelemetry` so
the public API is naturally idempotent, which is what services want
during hot-reload and test-suite iteration.
"""

from __future__ import annotations

from typing import Any


def instrument_fastapi(app: Any) -> None:
    """Instrument a FastAPI application with the OTel middleware.

    Uses the per-app form of `FastAPIInstrumentor` so multi-app
    processes can opt in selectively. No global state is touched.
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_boto3() -> None:
    """Instrument boto3/botocore globally.

    Idempotent: subsequent calls are no-ops. The Botocore instrumentor
    covers boto3 too because boto3 sits on top of botocore.
    """
    from opentelemetry.instrumentation.botocore import BotocoreInstrumentor

    instrumentor = BotocoreInstrumentor()  # type: ignore[no-untyped-call]
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()


def instrument_httpx() -> None:
    """Instrument httpx globally for both sync and async clients.

    Idempotent: subsequent calls are no-ops.
    """
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    instrumentor = HTTPXClientInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()
