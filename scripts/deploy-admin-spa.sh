#!/usr/bin/env bash
# deploy-admin-spa.sh: build, upload, and CloudFront-invalidate the
# Panakoes admin SPA (SvelteKit static adapter -> S3 + CloudFront).
#
# Today operators do this by hand each time a relevant SPA / infra PR
# lands (PR #225 / #235 / #238). This script collapses the sequence
# into one repeatable, dry-runnable invocation.
#
# Discovery strategy for bucket + CloudFront distribution id:
#   The frontend module (infra/dev/frontend/) is the single source of
#   truth: it owns the resources and exports the IDs via terraform
#   outputs (bucket_name, distribution_id). We `terraform init` that
#   module in read-only fashion against the remote S3 backend and
#   `terraform output -raw` to fetch them.
#
#   Why not `aws cloudfront list-distributions` + `aws s3api list-buckets`
#   with Name/Comment tag filters? Two reasons:
#     1. Tags on a CloudFront distribution are not surfaced in the
#        `list-distributions` payload; you would have to list and then
#        `list-tags-for-resource` per distribution (N+1 API calls).
#     2. The S3 bucket name carries a random_id suffix (see
#        infra/dev/frontend/main.tf), so a `--query "contains(Name,
#        'panakoes-dev-frontend')"` filter can match more than one
#        bucket if a destroy / recreate cycle has happened. Terraform
#        state is the only authoritative pointer.
#
#   The script never bakes account IDs, bucket names, or distribution
#   IDs as literals.
#
# Required tooling: aws, terraform, jq, pnpm (built-in if a fresh
# build is needed; you can skip the build by populating
# services/admin/build/ ahead of time and passing --skip-build).
#
# Required env (one of the following must be set):
#   AWS_PROFILE              Named profile. Used for local / operator runs.
#                            Must NOT be set when using OIDC env vars below.
#   AWS_ACCESS_KEY_ID +
#   AWS_SECRET_ACCESS_KEY +
#   AWS_SESSION_TOKEN        Ambient OIDC credentials on a GHA runner.
#                            Exported by aws-actions/configure-aws-credentials.
#
# Optional env:
#   AWS_REGION               default us-east-1
#   ADMIN_BUILD_DIR          override the SvelteKit build directory
#                            (default services/admin/build)
#
# CLI flags:
#   --dry-run                Print every command without executing.
#   --no-invalidate          Upload to S3 but skip CloudFront invalidation.
#   --skip-build             Re-use the existing build directory instead
#                            of running `pnpm --filter @panakoes/admin build`.
#   --env <dev|prod>         Which terraform stack to read outputs from.
#                            Default dev. Prod path is the same module
#                            under infra/prod/frontend/ once that exists;
#                            today only dev is wired.
#   --api-base-url <URL>     Override VITE_API_BASE_URL baked into the
#                            build. Default: the dev API Gateway URL.
#   -h | --help              Print usage and exit 0.
#
# Exit codes:
#   0  success
#   1  generic failure (build / upload / invalidation)
#   2  bad usage / required env missing / dependency missing
#   3  terraform state unreadable (init / output failure)
#
# shellcheck disable=SC2155

set -euo pipefail

# ---------------------------------------------------------------------
# Defaults
#
# Every VITE_* the SPA reads at build time gets a default here. Vite
# inlines these into the static bundle (see services/admin/src/lib/
# config.ts + otel.ts), so a missing default at bake time silently
# becomes the SPA's compile-time falsy default. Documenting the full
# set in one place keeps the deploy contract auditable and lets CI
# overrides be explicit per-environment.
# ---------------------------------------------------------------------
DEFAULT_API_BASE_URL="https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev"
DEFAULT_USE_LIVE_HEALTH_AGGREGATOR="true"
DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT=""
DEFAULT_SERVICE_VERSION=""
DEFAULT_DEPLOYMENT_ENVIRONMENT="dev"
DRY_RUN=0
NO_INVALIDATE=0
SKIP_BUILD=0
ENV_NAME="dev"
API_BASE_URL=""
USE_LIVE_HEALTH_AGGREGATOR=""
OTEL_EXPORTER_OTLP_ENDPOINT_VAL=""
SERVICE_VERSION=""
DEPLOYMENT_ENVIRONMENT=""
AWS_REGION="${AWS_REGION:-us-east-1}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADMIN_BUILD_DIR="${ADMIN_BUILD_DIR:-$REPO_ROOT/services/admin/build}"

# ---------------------------------------------------------------------
# Logging: never start a printf format string with `--`; route every
# user-facing line through log() which uses `printf '%s\n'`.
# ---------------------------------------------------------------------
log() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

usage() {
  cat <<'USAGE'
Usage: scripts/deploy-admin-spa.sh [options]

Build, upload, and CloudFront-invalidate the Panakoes admin SPA.

Options:
  --dry-run                 Print every command without executing.
  --no-invalidate           Upload to S3 but skip CloudFront invalidation.
                            Use for non-content-change deploys to save the
                            one free invalidation per month (CloudFront
                            charges $0.005 per path after the first 1,000).
  --skip-build              Re-use services/admin/build/ instead of running
                            pnpm build. Useful when CI built the bundle.
  --env <dev|prod>          Terraform stack to read bucket + distribution
                            id from. Default dev. Today only dev is wired.
  --api-base-url <URL>      Override VITE_API_BASE_URL baked into the build.
                            Default: dev API Gateway URL.
  --use-live-health-aggregator <true|false>
                            VITE_USE_LIVE_HEALTH_AGGREGATOR. When true, the
                            dashboard polls the live health-aggregator service;
                            when false it falls back to bundled static mocks.
                            Default: true.
  --otel-endpoint <URL>     VITE_OTEL_EXPORTER_OTLP_ENDPOINT. OTLP/HTTP
                            traces endpoint for the browser OTEL SDK.
                            Empty (default) puts the SDK in no-op mode.
  --service-version <VER>   VITE_SERVICE_VERSION. Maps to the `service.version`
                            span resource attribute. Default: the current git
                            short SHA (or 0.0.0 if git is unavailable).
  --deployment-environment <NAME>
                            VITE_DEPLOYMENT_ENVIRONMENT. Maps to the
                            `deployment.environment` resource attribute.
                            Default: dev.
  -h, --help                This message.

Required env (one of):
  AWS_PROFILE               Named profile with S3 write + CloudFront
                            create-invalidation perms (local / operator use).
  AWS_ACCESS_KEY_ID +
  AWS_SECRET_ACCESS_KEY +
  AWS_SESSION_TOKEN         Ambient OIDC credentials on a GHA runner.
                            Do NOT set AWS_PROFILE alongside these.

Optional env (each CLI flag accepts the same value from env if unset):
  AWS_REGION                Default us-east-1.
  ADMIN_BUILD_DIR           Override the build dir; default
                            services/admin/build.
  VITE_API_BASE_URL                  See --api-base-url.
  VITE_USE_LIVE_HEALTH_AGGREGATOR    See --use-live-health-aggregator.
  VITE_OTEL_EXPORTER_OTLP_ENDPOINT   See --otel-endpoint.
  VITE_SERVICE_VERSION               See --service-version.
  VITE_DEPLOYMENT_ENVIRONMENT        See --deployment-environment.

Exit codes:
  0  success
  1  generic failure
  2  bad usage / missing env / missing dependency
  3  terraform state unreadable
USAGE
}

# ---------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)         DRY_RUN=1; shift ;;
    --no-invalidate)   NO_INVALIDATE=1; shift ;;
    --skip-build)      SKIP_BUILD=1; shift ;;
    --env)
      [[ $# -ge 2 ]] || { err "ERROR: --env requires a value"; exit 2; }
      ENV_NAME="$2"; shift 2 ;;
    --api-base-url)
      [[ $# -ge 2 ]] || { err "ERROR: --api-base-url requires a value"; exit 2; }
      API_BASE_URL="$2"; shift 2 ;;
    --use-live-health-aggregator)
      [[ $# -ge 2 ]] || { err "ERROR: --use-live-health-aggregator requires a value"; exit 2; }
      USE_LIVE_HEALTH_AGGREGATOR="$2"; shift 2 ;;
    --otel-endpoint)
      [[ $# -ge 2 ]] || { err "ERROR: --otel-endpoint requires a value"; exit 2; }
      OTEL_EXPORTER_OTLP_ENDPOINT_VAL="$2"; shift 2 ;;
    --service-version)
      [[ $# -ge 2 ]] || { err "ERROR: --service-version requires a value"; exit 2; }
      SERVICE_VERSION="$2"; shift 2 ;;
    --deployment-environment)
      [[ $# -ge 2 ]] || { err "ERROR: --deployment-environment requires a value"; exit 2; }
      DEPLOYMENT_ENVIRONMENT="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    *)
      err "ERROR: unknown flag: $1"
      err "Run with --help for usage."
      exit 2 ;;
  esac
done

case "$ENV_NAME" in
  dev|prod) : ;;
  *) err "ERROR: --env must be dev or prod, got '$ENV_NAME'"; exit 2 ;;
esac

if [[ -z "$API_BASE_URL" ]]; then
  API_BASE_URL="${VITE_API_BASE_URL:-}"
fi
if [[ -z "$API_BASE_URL" ]]; then
  if [[ "$ENV_NAME" == "dev" ]]; then
    API_BASE_URL="$DEFAULT_API_BASE_URL"
  else
    err "ERROR: --api-base-url is required for --env $ENV_NAME"
    err "(no built-in default exists for non-dev envs)"
    exit 2
  fi
fi

# Resolve the rest of the VITE_* surface. Precedence per variable:
#   CLI flag > VITE_* env > documented default.
# Empty strings are valid for the OTLP endpoint and service version
# (both have meaningful empty defaults in services/admin/.env.example).
if [[ -z "$USE_LIVE_HEALTH_AGGREGATOR" ]]; then
  USE_LIVE_HEALTH_AGGREGATOR="${VITE_USE_LIVE_HEALTH_AGGREGATOR:-$DEFAULT_USE_LIVE_HEALTH_AGGREGATOR}"
fi
case "$USE_LIVE_HEALTH_AGGREGATOR" in
  true|false) : ;;
  *) err "ERROR: --use-live-health-aggregator must be 'true' or 'false', got '$USE_LIVE_HEALTH_AGGREGATOR'"; exit 2 ;;
esac

if [[ -z "$OTEL_EXPORTER_OTLP_ENDPOINT_VAL" ]]; then
  OTEL_EXPORTER_OTLP_ENDPOINT_VAL="${VITE_OTEL_EXPORTER_OTLP_ENDPOINT:-$DEFAULT_OTEL_EXPORTER_OTLP_ENDPOINT}"
fi

if [[ -z "$SERVICE_VERSION" ]]; then
  SERVICE_VERSION="${VITE_SERVICE_VERSION:-$DEFAULT_SERVICE_VERSION}"
fi
# Default service version: git short SHA when available, else 0.0.0.
# Lets ad-hoc deploys still carry a useful version attribute without
# requiring the operator to remember the flag.
if [[ -z "$SERVICE_VERSION" ]]; then
  if command -v git >/dev/null 2>&1 && SERVICE_VERSION=$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null); then
    : # SERVICE_VERSION populated by the assignment
  else
    SERVICE_VERSION="0.0.0"
  fi
fi

if [[ -z "$DEPLOYMENT_ENVIRONMENT" ]]; then
  DEPLOYMENT_ENVIRONMENT="${VITE_DEPLOYMENT_ENVIRONMENT:-$DEFAULT_DEPLOYMENT_ENVIRONMENT}"
fi

# ---------------------------------------------------------------------
# Pre-flight: AWS credentials + dependencies
#
# Two accepted credential modes:
#   (a) AWS_PROFILE set to a named profile (local / operator use).
#   (b) AWS_ACCESS_KEY_ID set in the environment (OIDC-sourced temp creds
#       from aws-actions/configure-aws-credentials on a GHA runner). In
#       this mode AWS_PROFILE must NOT be set; the aws CLI would otherwise
#       try to resolve a named profile, ignoring the exported key env vars.
# ---------------------------------------------------------------------
if [[ -z "${AWS_PROFILE:-}" && -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  err "ERROR: AWS credentials are required. Provide one of:"
  err "  AWS_PROFILE   - a named profile with S3 write + CloudFront"
  err "                  invalidation perms on the Panakoes $ENV_NAME account"
  err "                  e.g.:  export AWS_PROFILE=panakoes-admin"
  err "  AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY + AWS_SESSION_TOKEN"
  err "                - ambient OIDC credentials (set by aws-actions/"
  err "                  configure-aws-credentials on a GHA runner)"
  exit 2
fi

need() {
  command -v "$1" >/dev/null 2>&1 || {
    err "ERROR: required tool '$1' not found on PATH"
    exit 2
  }
}
need aws
need terraform
need jq
if [[ $SKIP_BUILD -eq 0 ]]; then
  need pnpm
fi

# ---------------------------------------------------------------------
# Dry-run helper: print, never execute.
# ---------------------------------------------------------------------
run() {
  if [[ $DRY_RUN -eq 1 ]]; then
    log "+ $*"
  else
    log "+ $*"
    eval "$@"
  fi
}

# Capture-style run: for commands whose stdout we consume. In dry-run
# mode we emit a placeholder so downstream logic can proceed without
# touching AWS / Terraform.
capture() {
  if [[ $DRY_RUN -eq 1 ]]; then
    log "+ $*" >&2
    printf '%s' "<dry-run-placeholder>"
  else
    eval "$@"
  fi
}

# ---------------------------------------------------------------------
# Step 1: discover bucket + distribution id from terraform output.
#
# We `terraform init -input=false` against the frontend module so the
# remote S3 backend is wired up, then `terraform output -raw` each
# value. If state is unreadable (no creds, wrong account, module never
# applied), exit 3 with an actionable hint.
# ---------------------------------------------------------------------
TF_MODULE_DIR="$REPO_ROOT/infra/$ENV_NAME/frontend"
if [[ ! -d "$TF_MODULE_DIR" ]]; then
  err "ERROR: terraform module not found at $TF_MODULE_DIR"
  err "(expected infra/$ENV_NAME/frontend/ to exist)"
  exit 3
fi

log "==> Discovering bucket + distribution id from $TF_MODULE_DIR"
if [[ $DRY_RUN -eq 0 ]]; then
  if ! ( cd "$TF_MODULE_DIR" && terraform init -input=false -reconfigure >/dev/null ); then
    err "ERROR: terraform init failed against $TF_MODULE_DIR"
    err "Common causes: AWS_PROFILE wrong account, S3 backend bucket"
    err "(panakoes-tf-state-*) not accessible, or KMS decrypt denied."
    exit 3
  fi
  BUCKET_NAME=$(cd "$TF_MODULE_DIR" && terraform output -raw bucket_name 2>/dev/null || true)
  DISTRIBUTION_ID=$(cd "$TF_MODULE_DIR" && terraform output -raw distribution_id 2>/dev/null || true)
  DISTRIBUTION_DOMAIN=$(cd "$TF_MODULE_DIR" && terraform output -raw distribution_domain_name 2>/dev/null || true)
  if [[ -z "$BUCKET_NAME" || -z "$DISTRIBUTION_ID" ]]; then
    err "ERROR: terraform outputs missing (bucket_name / distribution_id empty)."
    err "Has the frontend module been applied against this environment?"
    exit 3
  fi
else
  BUCKET_NAME="<dry-run-bucket>"
  DISTRIBUTION_ID="<dry-run-distribution-id>"
  DISTRIBUTION_DOMAIN="<dry-run-domain>.cloudfront.net"
fi
log "    bucket            = $BUCKET_NAME"
log "    distribution_id   = $DISTRIBUTION_ID"
log "    distribution_url  = https://$DISTRIBUTION_DOMAIN"

# ---------------------------------------------------------------------
# Step 2: build the SPA (unless --skip-build).
#
# Vite inlines every VITE_* var at build time, so the env is part of
# the bundle. We export the vars rather than prefixing the command so
# any pnpm-spawned subprocess sees the same values.
# ---------------------------------------------------------------------
if [[ $SKIP_BUILD -eq 0 ]]; then
  log "==> Building admin SPA"
  log "    VITE_API_BASE_URL                = $API_BASE_URL"
  log "    VITE_USE_LIVE_HEALTH_AGGREGATOR  = $USE_LIVE_HEALTH_AGGREGATOR"
  log "    VITE_OTEL_EXPORTER_OTLP_ENDPOINT = ${OTEL_EXPORTER_OTLP_ENDPOINT_VAL:-<empty: no-op exporter>}"
  log "    VITE_SERVICE_VERSION             = $SERVICE_VERSION"
  log "    VITE_DEPLOYMENT_ENVIRONMENT      = $DEPLOYMENT_ENVIRONMENT"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "+ VITE_API_BASE_URL=$API_BASE_URL \\"
    log "  VITE_USE_LIVE_HEALTH_AGGREGATOR=$USE_LIVE_HEALTH_AGGREGATOR \\"
    log "  VITE_OTEL_EXPORTER_OTLP_ENDPOINT=$OTEL_EXPORTER_OTLP_ENDPOINT_VAL \\"
    log "  VITE_SERVICE_VERSION=$SERVICE_VERSION \\"
    log "  VITE_DEPLOYMENT_ENVIRONMENT=$DEPLOYMENT_ENVIRONMENT \\"
    log "  (cd services/admin && pnpm build)"
  else
    (
      cd "$REPO_ROOT/services/admin" &&
      VITE_API_BASE_URL="$API_BASE_URL" \
      VITE_USE_LIVE_HEALTH_AGGREGATOR="$USE_LIVE_HEALTH_AGGREGATOR" \
      VITE_OTEL_EXPORTER_OTLP_ENDPOINT="$OTEL_EXPORTER_OTLP_ENDPOINT_VAL" \
      VITE_SERVICE_VERSION="$SERVICE_VERSION" \
      VITE_DEPLOYMENT_ENVIRONMENT="$DEPLOYMENT_ENVIRONMENT" \
      pnpm build
    )
  fi
else
  log "==> Skipping build (--skip-build)"
fi

if [[ $DRY_RUN -eq 0 && ! -d "$ADMIN_BUILD_DIR" ]]; then
  err "ERROR: build directory does not exist at $ADMIN_BUILD_DIR"
  err "Either run without --skip-build or set ADMIN_BUILD_DIR."
  exit 1
fi

# ---------------------------------------------------------------------
# Step 3: upload to S3 with per-file Cache-Control headers.
#
# Two passes:
#   (a) Hashed asset files under _app/immutable/ (SvelteKit's adapter-static
#       emits content-hashed filenames here). Long-cache immutable.
#   (b) Everything else (index.html, fallback.html, favicons, mock JSON).
#       Short-cache no-cache so a redeploy is reflected immediately.
#
# `aws s3 sync --delete` on the second pass removes stale files (any
# old hashed bundle that the immutable pass did not refresh). We run
# the immutable pass first WITHOUT --delete so the not-yet-uploaded
# index.html does not become a deletion candidate mid-sync.
# ---------------------------------------------------------------------
log "==> Uploading $ADMIN_BUILD_DIR to s3://$BUCKET_NAME/"

# (a) Long-cache immutable assets
run "aws s3 sync '$ADMIN_BUILD_DIR/_app/immutable' 's3://$BUCKET_NAME/_app/immutable' \
  --region '$AWS_REGION' \
  --cache-control 'public,max-age=31536000,immutable' \
  --only-show-errors"

# (b) Short-cache HTML + everything that is not under _app/immutable
run "aws s3 sync '$ADMIN_BUILD_DIR' 's3://$BUCKET_NAME' \
  --region '$AWS_REGION' \
  --delete \
  --exclude '_app/immutable/*' \
  --cache-control 'no-cache' \
  --only-show-errors"

# ---------------------------------------------------------------------
# Step 4: CloudFront invalidation.
#
# Default is /* (entire distribution). With --no-invalidate, skip
# the API call entirely (cheaper for non-content-change deploys). The
# first 1,000 invalidation paths per month are free; /* counts as one
# path so it stays inside the free tier under any reasonable cadence.
# ---------------------------------------------------------------------
INVALIDATION_ID=""
if [[ $NO_INVALIDATE -eq 1 ]]; then
  log "==> Skipping CloudFront invalidation (--no-invalidate)"
else
  log "==> Creating CloudFront invalidation for /* on $DISTRIBUTION_ID"
  if [[ $DRY_RUN -eq 1 ]]; then
    log "+ aws cloudfront create-invalidation --distribution-id $DISTRIBUTION_ID --paths '/*'"
    INVALIDATION_ID="<dry-run-invalidation-id>"
  else
    INVALIDATION_ID=$(aws cloudfront create-invalidation \
      --distribution-id "$DISTRIBUTION_ID" \
      --paths '/*' \
      --query 'Invalidation.Id' \
      --output text)
  fi
  log "    invalidation_id = $INVALIDATION_ID"
fi

# ---------------------------------------------------------------------
# Step 5: report.
# ---------------------------------------------------------------------
log ""
log "==> Deploy summary"
log "    env              = $ENV_NAME"
log "    bucket           = s3://$BUCKET_NAME"
log "    distribution     = $DISTRIBUTION_ID"
log "    public URL       = https://$DISTRIBUTION_DOMAIN"
if [[ -n "$INVALIDATION_ID" ]]; then
  log "    invalidation     = $INVALIDATION_ID (status: InProgress)"
  log "    check status     = aws cloudfront get-invalidation \\"
  log "                         --distribution-id $DISTRIBUTION_ID \\"
  log "                         --id $INVALIDATION_ID"
else
  log "    invalidation     = skipped"
fi
log ""
log "Done."
