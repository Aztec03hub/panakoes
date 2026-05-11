# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the gpu-spawner FastAPI service.
#
# Build context MUST be the repo root so the COPY of sibling path-deps
# (`services/audit-lib`) resolves. uv.lock pins it as an editable path-dep
# at `../audit-lib` relative to `services/gpu-spawner/`, so the in-image
# directory layout must mirror that.
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -f services/gpu-spawner/Dockerfile \
#     -t <ECR-URI>:tag \
#     .

FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

# Mirror the on-disk layout so uv's `../audit-lib` style path-deps resolve.
COPY services/audit-lib /workspace/audit-lib

COPY services/gpu-spawner/pyproject.toml /workspace/gpu-spawner/pyproject.toml
COPY services/gpu-spawner/uv.lock /workspace/gpu-spawner/uv.lock
COPY services/gpu-spawner/README.md /workspace/gpu-spawner/README.md
COPY services/gpu-spawner/src /workspace/gpu-spawner/src

WORKDIR /workspace/gpu-spawner

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/gpu-spawner/src/ ./src/

USER app

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "panakoes_gpu_spawner.main:app", "--host", "0.0.0.0", "--port", "8000"]
