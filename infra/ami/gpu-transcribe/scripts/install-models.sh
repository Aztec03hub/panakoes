#!/usr/bin/env bash
# install-models.sh
#
# Downloads and SHA256-verifies the three model artifacts the panakoes
# transcription pipeline depends on:
#   1. Whisper-large-v3 fp16 weights  -> $WHISPER_INSTALL_PATH
#   2. faster-whisper-large CT2 weights tarball -> $FASTER_WHISPER_INSTALL_DIR
#   3. Silero VAD JIT weights        -> $SILERO_VAD_INSTALL_PATH
#
# Required environment variables (set by the Packer provisioner):
#   WHISPER_LARGE_V3_URL, WHISPER_LARGE_V3_SHA256, WHISPER_INSTALL_PATH
#   FASTER_WHISPER_URL,   FASTER_WHISPER_SHA256,   FASTER_WHISPER_INSTALL_DIR
#   SILERO_VAD_URL,       SILERO_VAD_SHA256,       SILERO_VAD_INSTALL_PATH
#
# Hash verification uses `openssl sha256` rather than `sha256sum` because the
# Deep Learning AMI keeps openssl on the default PATH but coreutils digest
# tools occasionally drift across base-AMI revisions. openssl is more stable.
#
# Exit codes:
#   0  All artifacts downloaded, verified, and installed.
#   1  An expected env var is missing.
#   2  A download failed after retries.
#   3  A SHA256 verification failed.

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

# Download a URL to a destination path with retry-on-failure.
# Uses `curl -fL --retry` so transient 5xx and rate-limit hiccups self-heal
# without aborting the entire AMI build (which costs ~$0.05 per attempt).
download() {
    local url="$1"
    local dest="$2"
    log "Downloading $url -> $dest"
    sudo mkdir -p "$(dirname "$dest")"
    sudo curl \
        --fail \
        --location \
        --retry 5 \
        --retry-delay 5 \
        --retry-max-time 600 \
        --show-error \
        --silent \
        --output "$dest" \
        "$url" \
        || { echo "[install-models] ERROR: download failed for $url" >&2; exit 2; }
}

# Verify a file's SHA256 against an expected hex digest using openssl.
# openssl prints the digest as `SHA2-256(filename)= <hex>` on modern releases
# and `SHA256(filename)= <hex>` on older ones; we strip both prefixes so the
# comparison is portable across Deep Learning AMI revisions.
verify_sha256() {
    local path="$1"
    local expected="$2"
    log "Verifying SHA256 of $path"
    local actual
    actual=$(openssl dgst -sha256 "$path" | awk '{print $NF}')
    if [ "$actual" != "$expected" ]; then
        echo "[install-models] ERROR: SHA256 mismatch for $path" >&2
        echo "  expected: $expected" >&2
        echo "  actual:   $actual" >&2
        exit 3
    fi
    log "SHA256 OK for $path"
}

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------

require_var WHISPER_LARGE_V3_URL
require_var WHISPER_LARGE_V3_SHA256
require_var WHISPER_INSTALL_PATH
require_var FASTER_WHISPER_URL
require_var FASTER_WHISPER_SHA256
require_var FASTER_WHISPER_INSTALL_DIR
require_var SILERO_VAD_URL
require_var SILERO_VAD_SHA256
require_var SILERO_VAD_INSTALL_PATH

# ---------------------------------------------------------------------------
# 1. Whisper-large-v3 fp16 weights (batch path)
# ---------------------------------------------------------------------------

log "Installing Whisper-large-v3 fp16 weights"
download "$WHISPER_LARGE_V3_URL" "$WHISPER_INSTALL_PATH"
verify_sha256 "$WHISPER_INSTALL_PATH" "$WHISPER_LARGE_V3_SHA256"

# ---------------------------------------------------------------------------
# 2. faster-whisper-large CT2 weights (streaming path)
# ---------------------------------------------------------------------------

log "Installing faster-whisper-large weights"
sudo mkdir -p "$FASTER_WHISPER_INSTALL_DIR"
FASTER_WHISPER_TARBALL="/tmp/faster-whisper-large.tar.gz"
download "$FASTER_WHISPER_URL" "$FASTER_WHISPER_TARBALL"
verify_sha256 "$FASTER_WHISPER_TARBALL" "$FASTER_WHISPER_SHA256"

log "Unpacking faster-whisper-large into $FASTER_WHISPER_INSTALL_DIR"
sudo tar --extract \
    --gzip \
    --file "$FASTER_WHISPER_TARBALL" \
    --directory "$FASTER_WHISPER_INSTALL_DIR" \
    --strip-components=1
sudo rm -f "$FASTER_WHISPER_TARBALL"

# ---------------------------------------------------------------------------
# 3. Silero VAD weights (streaming path)
# ---------------------------------------------------------------------------

log "Installing Silero VAD weights"
download "$SILERO_VAD_URL" "$SILERO_VAD_INSTALL_PATH"
verify_sha256 "$SILERO_VAD_INSTALL_PATH" "$SILERO_VAD_SHA256"

# ---------------------------------------------------------------------------
# Lock down read-only state so a runaway runtime cannot rewrite weights
# ---------------------------------------------------------------------------

sudo chmod -R a-w /opt/whisper/models
sudo chown -R root:root /opt/whisper

log "All model artifacts installed and verified."
