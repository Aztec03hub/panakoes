# panakoes-otel

Shared OpenTelemetry instrumentation for Panakoes Python services. Every Python microservice imports this library to wire traces, metrics, and logs through a single consistent pipeline.

## Public API

```python
from panakoes_otel import (
    configure,
    instrument_fastapi,
    instrument_boto3,
    instrument_httpx,
    get_tracer,
    get_meter,
    shutdown,
)
```

### `configure(service_name, environment="dev", endpoint=None)`

Sets up the TracerProvider, MeterProvider, and LoggerProvider with OTLP/gRPC exporters and W3C TraceContext propagation. Idempotent.

Resource attributes set on every span/metric/log:

| Attribute | Source |
|---|---|
| `service.name` | The `service_name` argument |
| `service.namespace` | Always `panakoes` |
| `service.version` | Env var `SERVICE_VERSION`, default `0.0.0` |
| `deployment.environment` | The `environment` argument |

Endpoint resolution precedence:

1. The `endpoint` argument if non-`None`
2. The `OTEL_EXPORTER_OTLP_ENDPOINT` env var
3. `http://localhost:4317`

### `instrument_fastapi(app)`, `instrument_boto3()`, `instrument_httpx()`

Enable automatic instrumentation for the named library. All are idempotent.

### `get_tracer(name)`, `get_meter(name)`

Convenience getters that return tracers/meters bound to the configured provider. Safe to call before `configure()`; OTel returns proxy objects that activate once a provider lands.

### `shutdown()`

Flushes any buffered telemetry and tears down providers. Idempotent; safe to call multiple times. Wire this into your service's shutdown hook (FastAPI lifespan exit, atexit, etc.). Also uninstalls the error-capture hooks.

### `install_exception_capture()` / `uninstall_exception_capture()`

Auto-invoked by `configure()` so consuming services get error capture for free. Four hooks are wired:

1. `sys.excepthook` for synchronous uncaught exceptions in the main thread.
2. `threading.excepthook` for worker thread crashes.
3. The current asyncio loop's `default_exception_handler` for orphaned coroutine failures.
4. A `logging` filter (NOT a handler, so it works regardless of the host's handler topology) that mirrors `ERROR` and `CRITICAL` records as events on the active span with `log.severity`, `log.message`, `log.logger` attributes.

Each path records the exception on the currently active span when one is available; otherwise it starts a one-shot `uncaught_exception` span so the failure is never silently dropped. On uncaught paths the tracer provider is force-flushed before re-delegating to the original hook.

The capture is opt-out: set `OTEL_DISABLE_ERROR_CAPTURE=true` to skip the install. Defaults to on because errors going untracked is strictly worse than test noise. Tests that intentionally throw should set this variable in their fixture.

### Configuration (env vars)

| Variable | Default | Notes |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` to wire NoOp providers (tests, offline dev) |
| `OTEL_DISABLE_ERROR_CAPTURE` | (unset) | Set to `true` to skip auto-installing the error-capture hooks |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |

## Installation

Within the monorepo, declared as a path dependency in each consuming service's `pyproject.toml`:

```toml
[project]
dependencies = [
    "panakoes-otel @ file:../otel-lib",
]

[tool.uv.sources]
panakoes-otel = { path = "../otel-lib", editable = true }
```

## Usage examples

**Configure once at startup, instrument the standard libraries, expose a tracer:**

```python
import panakoes_otel
from fastapi import FastAPI

panakoes_otel.configure(service_name="ingestion-api", environment="prod")

app = FastAPI()
panakoes_otel.instrument_fastapi(app)
panakoes_otel.instrument_boto3()
panakoes_otel.instrument_httpx()

tracer = panakoes_otel.get_tracer(__name__)
meter = panakoes_otel.get_meter(__name__)
```

**Flush exporters on shutdown:**

```python
@app.on_event("shutdown")
async def _shutdown() -> None:
    panakoes_otel.shutdown()
```

**Disable telemetry inside tests or offline runs:**

```bash
export OTEL_SDK_DISABLED=true
uv run pytest
```

## Coverage requirement

80% per ADR-018 (library tier; this is not a security-critical service). The `--cov-fail-under=80` gate is wired into `pyproject.toml`; CI fails the PR below threshold.

```bash
uv sync --group dev
uv run ruff check
uv run mypy src
uv run pytest
```

Tests run with `OTEL_SDK_DISABLED` cleared by the autouse fixture; instrumentation tests use the real SDK providers but never open network connections to a live collector. Per-test cleanup resets module-level state and uninstruments any `Instrumentor` it touched.

## Architecture notes

The Observability ADR commits Panakoes to OpenTelemetry as the vendor-neutral instrumentation layer, exporting via OTLP/gRPC to AWS Distro for OpenTelemetry (ADOT), which fans out to CloudWatch + X-Ray. Every service needs the same boilerplate: TracerProvider, MeterProvider, LoggerProvider, OTLP exporters, W3C TraceContext propagation, and resource attributes that identify the service in the backend dashboards. Centralizing this in one library means a single import + one `configure()` call replaces ~50 lines of OTel setup per service, and the resource convention stays consistent across the fleet.
