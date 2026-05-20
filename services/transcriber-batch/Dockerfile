# syntax=docker/dockerfile:1.7
#
# transcriber-batch container image (self-contained Whisper-large-v3 fp16).
#
# Base image: python:3.12 (full, not slim) so the openai-whisper + torch
# wheels install cleanly without a bunch of system-package compilation.
# PyTorch CUDA wheels (torch==2.5.x+cu124) bundle the CUDA runtime
# libraries inside the wheel, so this container does NOT require the
# host AMI to mount CUDA libs in via the NVIDIA Container Toolkit. The
# AWS Deep Learning Base GPU AMI provides the NVIDIA driver + Container
# Toolkit, which is enough to expose the GPU device file (/dev/nvidia0)
# to the container; torch's bundled CUDA does the rest.
#
# The Whisper model weights (~3 GB for large-v3 fp16) are downloaded
# on first run via openai-whisper's built-in fetcher. Subsequent runs
# on the same container instance reuse the cached weights. Spot
# instances are ephemeral, so each new Spot host pays the download
# cost once (~30 s on a GPU host's network).
#
# A future-optimization custom AMI (see infra/ami/gpu-transcribe/) can
# pre-bake the weights to /opt/whisper/models/large-v3.pt and have the
# container honor MODEL_PATH; the current container falls back to the
# auto-download if MODEL_PATH is unset or the file is absent.
#
# Build context MUST be the repo root:
#   docker buildx build -f services/transcriber-batch/Dockerfile \
#     --platform linux/amd64 -t <ECR>:<tag> --push .

FROM python:3.12 AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace

# Path-dep siblings the transcriber-batch service consumes. Each must
# be mounted at the same relative location the pyproject.toml's
# [tool.uv.sources] block references (../<lib>).
COPY services/models-lib /workspace/models-lib
COPY services/otel-lib /workspace/otel-lib
COPY services/audit-lib /workspace/audit-lib
COPY services/transcriber-lib /workspace/transcriber-lib

COPY services/transcriber-batch/pyproject.toml /workspace/transcriber-batch/pyproject.toml
COPY services/transcriber-batch/README.md /workspace/transcriber-batch/README.md
COPY services/transcriber-batch/src /workspace/transcriber-batch/src

WORKDIR /workspace/transcriber-batch

# `--no-frozen` because uv.lock is generated post-merge by CI; if a
# lockfile exists in the source tree, uv will honor it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

FROM python:3.12 AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg is required by openai-whisper for audio decoding. The bullseye-
# slim base ships an older ffmpeg; the full python:3.12 base lets us
# install via apt without compiling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --no-create-home --home /tmp app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/transcriber-batch/src/ ./src/

# Cache directory for Whisper model weights. openai-whisper defaults to
# `~/.cache/whisper/`; we redirect to /tmp/whisper so the app user
# (which has no real home) can write there. Container instances reuse
# the cached weights for subsequent jobs.
ENV XDG_CACHE_HOME=/tmp/whisper-cache \
    HOME=/tmp \
    PYTHONPATH=/app/src

USER app

CMD ["python", "-m", "panakoes_transcriber_batch.main"]
