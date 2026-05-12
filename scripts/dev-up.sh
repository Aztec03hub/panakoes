#!/usr/bin/env bash
# scripts/dev-up.sh: bring up the local Panakoes dev stack.
#
# Idempotent: re-running is safe. Each service is only started if not
# already healthy. Echoes the connection strings every Panakoes service
# expects in its environment.
#
# Usage:
#   scripts/dev-up.sh                  # postgres + dynamodb-local
#   DEV_LOCALSTACK=1 scripts/dev-up.sh # also bring up localstack
#
# Why this exists:
# Spinning the full stack via `docker compose up` waits on every health
# check serially and is noisy. This script starts only what is missing
# and emits a copy-pasteable env block at the end so service startup is
# a one-liner from a fresh shell.
#
# Tooling assumptions (per CLAUDE.md):
#   - Docker + docker compose v2 plugin available.
#   - WSL2 on Windows or native Linux.

set -euo pipefail

# -----------------------------------------------------------------------------
# Resolve repo root regardless of how the script is invoked.
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

COMPOSE_FILE="${REPO_ROOT}/docker-compose.yml"

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker not found on PATH. Install Docker Desktop or docker-ce." >&2
    exit 1
fi

# Prefer `docker compose` (v2 plugin) over the legacy `docker-compose` binary.
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose -f "${COMPOSE_FILE}")
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose -f "${COMPOSE_FILE}")
else
    echo "ERROR: neither 'docker compose' nor 'docker-compose' is available." >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Helpers.
# -----------------------------------------------------------------------------
container_running() {
    local name="$1"
    docker ps --format '{{.Names}}' | grep -Fxq "${name}"
}

start_service() {
    local svc="$1"
    local container="$2"
    if container_running "${container}"; then
        echo "[dev-up] ${svc} already running (container ${container}); skipping."
        return 0
    fi
    echo "[dev-up] starting ${svc}..."
    "${DC[@]}" up -d --wait "${svc}"
}

start_localstack() {
    if container_running "panakoes-localstack"; then
        echo "[dev-up] localstack already running; skipping."
        return 0
    fi
    echo "[dev-up] starting localstack (S3, DynamoDB, EventBridge, SNS, SQS)..."
    "${DC[@]}" --profile localstack up -d --wait localstack
}

# -----------------------------------------------------------------------------
# Boot sequence.
# -----------------------------------------------------------------------------
start_service "postgres" "panakoes-postgres"
start_service "dynamodb-local" "panakoes-dynamodb-local"

if [ "${DEV_LOCALSTACK:-0}" = "1" ]; then
    start_localstack
else
    echo "[dev-up] skipping localstack; set DEV_LOCALSTACK=1 to enable."
fi

# -----------------------------------------------------------------------------
# Echo connection strings the services expect.
# -----------------------------------------------------------------------------
cat <<'ENV'

[dev-up] stack is up. Export these in your shell or copy into a service .env:

  export DATABASE_URL="postgresql://panakoes:panakoes@localhost:5432/panakoes"
  export DDB_ENDPOINT_URL="http://localhost:8000"
  export AWS_ENDPOINT_URL="http://localhost:4566"
  export AWS_ACCESS_KEY_ID="test"
  export AWS_SECRET_ACCESS_KEY="test"
  export AWS_DEFAULT_REGION="us-east-1"

Notes:
  - DATABASE_URL targets the postgres container.
  - DDB_ENDPOINT_URL is dynamodb-local (used by services that talk to
    DynamoDB; AWS SDKs honor this via endpoint_url overrides).
  - AWS_ENDPOINT_URL targets localstack and is only meaningful if you
    started with DEV_LOCALSTACK=1.
  - The "test"/"test" AWS credentials are the localstack convention; they
    are dummy values, not real secrets.

Tear down with: make dev-down
Wipe volumes with: docker compose down -v

ENV
