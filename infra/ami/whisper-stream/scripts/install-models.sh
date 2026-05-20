#!/usr/bin/env bash
# install-models.sh
#
# Downloads the faster-whisper-large-v2 CTranslate2 weights from
# HuggingFace into the canonical AMI path, then synthesizes a 1-second
# silent 16 kHz mono PCM warmup clip with ffmpeg.
#
# Why huggingface-cli instead of direct URL + SHA256 (as gpu-transcribe
# uses): the Systran/faster-whisper-large-v2 repo is a directory with
# multiple files (model.bin, config.json, tokenizer.json, vocabulary.txt)
# whose layout the HF CLI already knows. Tarball-and-SHA would either
# require maintaining a derived tarball in S3 or force per-file SHA
# pinning. HF model repos are content-addressed by revision SHA; pinning
# via $HF_MODEL_REVISION is the path forward when reproducibility matters.
#
# Why `--local-dir-use-symlinks False`: by default the HF CLI materializes
# the cached files as symlinks to the cache directory (~/.cache/huggingface).
# A baked AMI needs the actual files at the destination so the cache dir
# can be removed afterwards (saves ~3 GB of duplicate weights in the AMI).
#
# Required environment variables (set by the Packer provisioner):
#   MODEL_CACHE_DIR    Parent directory for the model bake (e.g. /opt/whisper/models)
#   MODEL_SIZE         Model variant; the final dir is "${MODEL_SIZE}-ct2"
#   WARMUP_CLIP_PATH   Absolute path to write the 1-second silence WAV
#   HF_MODEL_REPO      HuggingFace repo to download (e.g. Systran/faster-whisper-large-v2)
#
# Exit codes:
#   0  All artifacts downloaded and warmup clip synthesized.
#   1  An expected env var is missing.
#   2  A download or system-package install failed.
#   3  Post-install verification failed (expected files absent).

set -euo pipefail

log() {
    printf '[install-models] %s\n' "$*"
}

require_var() {
    local name="$1"
    if [ -z "${!name:-}" ]; then
        echo "[install-models] ERROR: required env var $name is unset" >&2
        exit 1
    fi
}

require_var MODEL_CACHE_DIR
require_var MODEL_SIZE
require_var WARMUP_CLIP_PATH
require_var HF_MODEL_REPO

MODEL_DIR="${MODEL_CACHE_DIR}/${MODEL_SIZE}-ct2"

log "Installing system prerequisites (python3-pip, ffmpeg)"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3-pip \
    ffmpeg \
    || { echo "[install-models] ERROR: apt-get install failed" >&2; exit 2; }

log "Installing huggingface-hub (provides huggingface-cli)"
# Pinning huggingface-hub to a recent stable. The CLI has been backwards
# compatible since the 0.20 series; a floor is more important than an
# exact pin. We do NOT use --no-deps because the CLI pulls a small set of
# transitive deps (filelock, tqdm, requests) that we want.
sudo pip3 install --no-cache-dir "huggingface-hub>=0.26,<1.0" \
    || { echo "[install-models] ERROR: pip3 install huggingface-hub failed" >&2; exit 2; }

log "Downloading ${HF_MODEL_REPO} into ${MODEL_DIR}"
sudo mkdir -p "$MODEL_DIR"
sudo huggingface-cli download "$HF_MODEL_REPO" \
    --local-dir "$MODEL_DIR" \
    --local-dir-use-symlinks False \
    || { echo "[install-models] ERROR: huggingface-cli download failed" >&2; exit 2; }

# huggingface-cli download materializes everything from the repo. For a
# baked AMI we only need the runtime files; drop the .gitattributes and
# README.md to keep the AMI tidy (saves a few KB, more importantly avoids
# confusion in audits about whether the AMI should ship project READMEs).
sudo rm -f "$MODEL_DIR/.gitattributes" "$MODEL_DIR/README.md" || true

# Drop the HF cache. By default huggingface-cli also keeps a copy in the
# user-level cache directory (~root/.cache/huggingface on the build host).
# With --local-dir-use-symlinks False the AMI copy is already independent,
# so the cache is pure dead weight.
sudo rm -rf /root/.cache/huggingface /home/ubuntu/.cache/huggingface || true

log "Synthesizing warmup clip at ${WARMUP_CLIP_PATH}"
sudo mkdir -p "$(dirname "$WARMUP_CLIP_PATH")"
# Generate a 1-second silent 16 kHz mono signed-16-bit PCM WAV. The
# warmup's job is to load the model graph onto the GPU and resolve any
# lazy CUDA-kernel JITs before the first real frame arrives; the content
# does not matter, so silence is fine and trivially reproducible.
sudo ffmpeg -y \
    -f lavfi -i "anullsrc=channel_layout=mono:sample_rate=16000" \
    -t 1 \
    -c:a pcm_s16le \
    "$WARMUP_CLIP_PATH" \
    || { echo "[install-models] ERROR: ffmpeg warmup-clip synthesis failed" >&2; exit 2; }

log "Verifying baked artifacts exist and are readable"
test -d "$MODEL_DIR" || { echo "[install-models] ERROR: $MODEL_DIR missing post-install" >&2; exit 3; }
test -s "$MODEL_DIR/model.bin" || { echo "[install-models] ERROR: model.bin missing or empty" >&2; exit 3; }
test -s "$MODEL_DIR/config.json" || { echo "[install-models] ERROR: config.json missing or empty" >&2; exit 3; }
test -s "$MODEL_DIR/tokenizer.json" || { echo "[install-models] ERROR: tokenizer.json missing or empty" >&2; exit 3; }
test -s "$WARMUP_CLIP_PATH" || { echo "[install-models] ERROR: warmup clip missing or empty" >&2; exit 3; }

log "Locking down read-only state so a runaway runtime cannot rewrite weights"
sudo chmod -R a-w "$MODEL_CACHE_DIR"
sudo chown -R root:root /opt/whisper

log "Reporting baked artifact sizes for audit:"
sudo du -sh "$MODEL_DIR" "$WARMUP_CLIP_PATH" || true

log "All streaming-path artifacts installed and verified."
