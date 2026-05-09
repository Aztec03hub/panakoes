# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml ./
COPY uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app src/ ./src/

USER app

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "panakoes_admin_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
