# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the template Python service. New services
# copy this file as their starting point.
#
# Build context MUST be the repo root so the COPY of sibling path-deps
# (`services/otel-lib`, and any other libs the service depends on like
# `services/audit-lib`, `services/auth-client`, `services/middleware-lib`)
# resolves. uv.lock pins these as editable path-deps at `../<lib>`
# relative to `services/<svc>/`, so the in-image directory layout must
# mirror that.
#
# When copying this template for a new service:
#   1. Replace every occurrence of `_template` with your service slug.
#   2. Replace `template_service.main` with your Python module path.
#   3. Add additional `COPY services/<lib> /workspace/<lib>` lines for
#      any other path-dep libraries your service depends on (check your
#      pyproject.toml `[tool.uv.sources]` for `../<lib>` entries).
#
# Build:
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -f services/<svc>/Dockerfile \
#     -t <ECR-URI>:tag \
#     .

FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

# Mirror the on-disk layout so uv's `../otel-lib` style path-deps resolve.
# Add more COPY lines here for additional sibling libs the service uses.
COPY services/otel-lib /workspace/otel-lib

COPY services/_template/pyproject.toml /workspace/_template/pyproject.toml
COPY services/_template/uv.lock /workspace/_template/uv.lock
COPY services/_template/README.md /workspace/_template/README.md
COPY services/_template/src /workspace/_template/src

WORKDIR /workspace/_template

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/_template/src/ ./src/

USER app

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "template_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
