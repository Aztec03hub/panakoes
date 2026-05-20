# syntax=docker/dockerfile:1.7
#
# transcriber-stream container image. Per-session, runs on a
# session-spawned g4dn.xlarge Spot instance.
#
# Base: python:3.12 (full, not slim) so faster-whisper + librosa +
# onnxruntime + torch CUDA wheels install cleanly without compiling
# system packages. PyTorch CUDA wheels (torch==2.5.x+cu124) bundle the
# CUDA runtime libraries inside the wheel, so this container does NOT
# require the host AMI to mount CUDA libs in via the NVIDIA Container
# Toolkit. The Spot host's GPU AMI provides the NVIDIA driver +
# Container Toolkit, which is enough to expose /dev/nvidia0 to the
# container; the bundled CUDA in the wheel does the rest.
#
# faster-whisper-large-v2 weights (~1.5 GB CTranslate2 format) are
# pre-baked into the host AMI at /opt/whisper/models/large-v2-ct2/.
# The container's startup assertion in main.py checks for both the
# weights directory AND the warmup clip at /opt/whisper/warmup-1s.wav
# BEFORE invoking the (slow) backend_factory, so a missing bake fails
# fast instead of silently HF-falling-back to a multi-minute download.
#
# Build context = REPO ROOT (matches the GHA image-bake workflow
# convention; the build-push-action sets `context: .` and `file:
# services/transcriber-stream/Dockerfile`). All COPY paths are
# repo-root-relative for that reason; local `docker build` from this
# directory must therefore set the context to the repo root, e.g.
# `docker build -f services/transcriber-stream/Dockerfile .`.

FROM python:3.12 AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.11.11 /uv /uvx /usr/local/bin/

WORKDIR /workspace/transcriber-stream

COPY services/transcriber-stream/pyproject.toml ./pyproject.toml
COPY services/transcriber-stream/README.md ./README.md
COPY services/transcriber-stream/NOTICE ./NOTICE
COPY services/transcriber-stream/src ./src

# --no-frozen because uv.lock is generated post-merge by CI; if a
# lockfile exists in the source tree, uv will honor it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

FROM python:3.12 AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ffmpeg + libsndfile for librosa / soundfile audio decoding.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --no-create-home --home /tmp app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app services/transcriber-stream/NOTICE ./NOTICE
COPY --chown=app:app services/transcriber-stream/src/ ./src/

# /opt/whisper is host-AMI-baked. The container's startup assertion
# checks for the model weights + warmup clip there before invoking
# the backend factory; we do NOT bake them into the image because the
# weights (~1.5 GB) belong to the immutable AMI layer for fast cold
# starts.
ENV PANAKOES_OBSERVABILITY_NAMESPACE=panakoes/streaming \
    PYTHONPATH=/app/src

USER app

CMD ["python", "-m", "panakoes_transcriber_stream.main"]
