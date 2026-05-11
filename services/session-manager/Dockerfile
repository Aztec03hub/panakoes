# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the session-manager FastAPI service.
#
# Build context MUST be the repo root so the COPY of sibling path-deps
# (`services/audit-lib`) resolves. uv.lock pins it as an editable path-dep
# at `../audit-lib` relative to `services/session-manager/`, so the in-image
# directory layout must mirror that.
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -f services/session-manager/Dockerfile \
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

COPY services/session-manager/pyproject.toml /workspace/session-manager/pyproject.toml
COPY services/session-manager/uv.lock /workspace/session-manager/uv.lock
COPY services/session-manager/README.md /workspace/session-manager/README.md
COPY services/session-manager/src /workspace/session-manager/src

WORKDIR /workspace/session-manager

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/session-manager/src/ ./src/

USER app

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "panakoes_session_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
