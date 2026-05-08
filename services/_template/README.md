# services/_template

This is the Python microservice skeleton for Panakoes. Every new Python service in this monorepo starts by copying this directory and renaming the package.

## What this service does

Nothing yet. It is a working FastAPI app with a `/health` endpoint, structured logging via `structlog`, and the full test, lint, and type-check tooling already wired up. Treat it as the baseline that real services extend.

## Using the template

1. Copy this directory to `services/<your-service>/`.
2. Rename `services/<your-service>/src/template_service/` to `services/<your-service>/src/<your_service>/`.
3. Update `pyproject.toml`:
   - `[project].name` to `panakoes-<your-service>`.
   - `[tool.hatch.build.targets.wheel].packages` to `["src/<your_service>"]`.
4. Update imports in `src/<your_service>/main.py` and `tests/` (search-replace `template_service` with `<your_service>`).
5. Update the default `service_name` in `src/<your_service>/config.py`.
6. Update the smoke test in `tests/integration/test_health.py` to match the new service name.
7. Update `Dockerfile` `CMD` to use the new module path.
8. Replace this README with service-specific documentation.

## Running locally

```bash
uv sync --group dev
uv run uvicorn template_service.main:app --reload
```

The app then listens on `http://127.0.0.1:8000` and `GET /health` returns `{"status": "ok", "service": "template"}`.

## Environment variables

The skeleton ships with the shared OpenTelemetry wiring (`panakoes-otel`)
already in place. Every service that copies the template inherits these
variables; tune them per deploy.

| Variable | Default | Notes |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP/gRPC collector endpoint (ADOT in prod) |
| `OTEL_SDK_DISABLED` | (unset) | Set to `true` in tests + offline dev to wire NoOp providers |
| `SERVICE_VERSION` | `0.0.0` | Stamped onto the `service.version` resource attribute |
| `DEPLOYMENT_ENVIRONMENT` | `dev` | Stamped onto the `deployment.environment` resource attribute |

## Running tests

```bash
uv run pytest
```

The repo-root `Makefile` exposes the same target across every service:

```bash
make test           # all tests in every service
make test-unit      # only unit-marked tests
make test-integration  # only integration-marked tests
```

Coverage is enforced at 80% by default per ADR-018. Auth, billing, and audit code paths are held to 100%; tighten the `--cov-fail-under` value in those services accordingly.

## Linting

```bash
uv run ruff check
```

## Type checking

```bash
uv run mypy src
```

The template runs `mypy` in strict mode. Add `# type: ignore[<error-code>]` only with a justification comment.

## Building the Docker image

```bash
docker build -t panakoes-<your-service> .
```

The Dockerfile is multi-stage: a builder stage installs `uv`-managed dependencies into `/opt/venv`, and the runtime stage copies that virtualenv plus the source tree into a minimal `python:3.12-slim` image, running as a non-root `app` user on port 8000.
