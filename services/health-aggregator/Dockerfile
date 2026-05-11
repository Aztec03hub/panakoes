# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the health-aggregator FastAPI service.
#
# Build context MUST be the repo root so the COPY of sibling path-deps
# (`services/auth-client`, `services/otel-lib`) resolves. uv.lock pins
# these as editable path-deps at `../auth-client`, `../otel-lib` relative
# to `services/health-aggregator/`, so the in-image directory layout
# must mirror that.
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -f services/health-aggregator/Dockerfile \
#     -t <ECR-URI>:tag \
#     .

FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

# Mirror the on-disk layout so uv's `../auth-client` style path-deps resolve.
COPY services/auth-client /workspace/auth-client
COPY services/otel-lib /workspace/otel-lib

COPY services/health-aggregator/pyproject.toml /workspace/health-aggregator/pyproject.toml
COPY services/health-aggregator/uv.lock /workspace/health-aggregator/uv.lock
COPY services/health-aggregator/README.md /workspace/health-aggregator/README.md
COPY services/health-aggregator/src /workspace/health-aggregator/src

WORKDIR /workspace/health-aggregator

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/health-aggregator/src/ ./src/

USER app

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "panakoes_health_aggregator.main:app", "--host", "0.0.0.0", "--port", "8000"]
