#!/usr/bin/env bash
# tf.sh: Panakoes Terraform apply-walkthrough helper.
#
# Wraps the dev-environment apply ritual into a single command per step:
# sync local main, init the module, plan with logging + summary extraction,
# apply with the saved plan, verify outputs. Designed to be paired with the
# operator-actions guide at docs/operator/aws-cloudflare-actions.md.
#
# Subcommands:
#   plan <module>    Sync local main, cd to infra/dev/<module>, terraform init -upgrade,
#                    terraform plan -out=tfplan with output captured to /tmp/.
#                    Prints a compact summary suitable for review.
#   apply <module>   terraform apply tfplan in infra/dev/<module>. Removes tfplan
#                    after a successful apply. Prints the apply tail + outputs.
#   verify <module>  terraform output for the module. Read-only.
#
# Usage:
#   scripts/tf.sh plan network
#   scripts/tf.sh apply network
#   scripts/tf.sh verify network
#
# Module names match directory names under infra/dev/ (and infra/ for global +
# bootstrap). Examples: network, data, admin-state, storage, secrets, ecr,
# iam, observability, events, security, auth-db, vpc-endpoints, waf, backup,
# api-gateway, step-functions, batch, frontend.
#
# Requires:
#   - AWS_PROFILE exported in your shell (e.g. export AWS_PROFILE=panakoes-admin).
#     Terraform's S3 backend won't authenticate without it.
#   - terraform >= 1.10 on PATH (use_lockfile S3 backend support).
#   - You are on the `main` branch. The script aborts on any other branch
#     because the apply walkthrough is supposed to run against current main,
#     not against in-flight feature branches.
#
# Exit codes:
#   0  command succeeded
#   1  not in a git repo / not on main / cannot resolve module dir
#   2  bad usage (missing subcommand or module argument)
#   3  module directory does not exist
#   4  apply attempted without a saved tfplan (run plan first)
#   5  on a non-main branch (refuses to proceed)
#   6  AWS_PROFILE not set

set -euo pipefail

# Strip ANSI escape sequences. Used when extracting summary lines from a
# log file that has Terraform's color codes embedded; we keep color in the
# live terminal output and in the log file itself, but the grep over the
# log needs to see the bare text. Matches CSI sequences (ESC [ ... letter)
# which covers all the SGR codes Terraform emits.
strip_ansi() {
  sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g'
}

# ---------------------------------------------------------------------------
# Pre-flight: repo + AWS profile
# ---------------------------------------------------------------------------

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  echo "tf.sh: not inside a git repo" >&2
  exit 1
fi

if [ -z "${AWS_PROFILE:-}" ]; then
  echo "tf.sh: AWS_PROFILE not set." >&2
  echo "       Run: export AWS_PROFILE=panakoes-admin" >&2
  exit 6
fi

cmd="${1:-}"
module="${2:-}"

if [ -z "$cmd" ] || [ -z "$module" ]; then
  echo "Usage: $0 {plan|apply|verify} <module>" >&2
  echo "       e.g. $0 plan network" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Resolve module directory
#
# infra/dev/<module> is the primary location. Fall back to infra/<module> for
# bootstrap + global which live one level higher. Anything else is an error.
# ---------------------------------------------------------------------------

if [ -d "$REPO_ROOT/infra/dev/$module" ]; then
  module_dir="$REPO_ROOT/infra/dev/$module"
elif [ -d "$REPO_ROOT/infra/$module" ]; then
  module_dir="$REPO_ROOT/infra/$module"
else
  echo "tf.sh: module '$module' not found at infra/dev/$module or infra/$module" >&2
  exit 3
fi

# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

cmd_plan() {
  echo "==> tf.sh plan $module"

  echo "==> Syncing local main from origin"
  cd "$REPO_ROOT"
  current_branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "DETACHED")
  if [ "$current_branch" != "main" ]; then
    echo "tf.sh: you are on branch '$current_branch', not main." >&2
    echo "       The apply walkthrough must run from main against current state." >&2
    echo "       Either: git checkout main && git pull --ff-only" >&2
    echo "       Or: run terraform commands manually if you know what you're doing." >&2
    exit 5
  fi
  git pull --ff-only origin main

  cd "$module_dir"
  echo
  echo "==> terraform init -upgrade  ($module_dir)"
  # -upgrade is idempotent when no providers are stale; safe to run every plan.
  # -input=false fails fast on any prompt rather than hanging.
  #
  # When backend config changes (e.g., the dynamodb_table -> use_lockfile
  # migration in PR #162), terraform init refuses with "Backend configuration
  # changed" and asks for -reconfigure or -migrate-state. Detect that one
  # specific error and retry with -reconfigure automatically; any other
  # init failure still propagates.
  init_log=$(mktemp)
  if ! terraform init -upgrade -input=false 2>&1 | tee "$init_log"; then
    if strip_ansi < "$init_log" | grep -q "Backend configuration changed"; then
      echo
      echo "==> Backend config changed; retrying with terraform init -reconfigure"
      terraform init -reconfigure -input=false
    else
      rm -f "$init_log"
      exit 1
    fi
  fi
  rm -f "$init_log"

  echo
  echo "==> terraform plan -out=tfplan"
  log="/tmp/tf-plan-${module}.log"
  terraform plan -out=tfplan -input=false 2>&1 | tee "$log"

  echo
  echo "================================================================="
  echo "  PLAN SUMMARY for $module"
  echo "================================================================="
  echo
  echo "-- Resource changes:"
  strip_ansi < "$log" | grep -E "^Plan:|^No changes" | head -3 \
    || echo "  (no Plan: line found in log)"
  echo
  echo "-- Output changes:"
  # Extract the "Changes to Outputs:" block (header + lines through the
  # next blank line). The trailing blank line is dropped via `sed '$d'`.
  output_block=$(strip_ansi < "$log" | sed -n '/^Changes to Outputs:/,/^$/p' | sed '$d')
  if [ -n "$output_block" ]; then
    echo "$output_block"
  else
    echo "  (no output changes)"
  fi
  echo
  # `grep -c` prints "0" and exits 1 when nothing matches. With `|| true`
  # we mask the exit code without doubling the count via a fallback echo.
  warning_count=$(strip_ansi < "$log" | grep -cE "^\| Warning:" || true)
  if [ "${warning_count:-0}" -gt 0 ]; then
    echo "-- Warnings: $warning_count"
    strip_ansi < "$log" | grep -A1 -E "^\| Warning:" | head -20 || true
    echo
  fi
  echo "Saved plan:  $module_dir/tfplan"
  echo "Full log:    $log"
  echo "Next step:   $0 apply $module"
}

# ---------------------------------------------------------------------------
# Subcommand: apply
# ---------------------------------------------------------------------------

cmd_apply() {
  cd "$module_dir"
  if [ ! -f tfplan ]; then
    echo "tf.sh: no saved tfplan in $module_dir" >&2
    echo "       Run: $0 plan $module" >&2
    exit 4
  fi

  echo "==> tf.sh apply $module"
  log="/tmp/tf-apply-${module}.log"
  terraform apply -input=false tfplan 2>&1 | tee "$log"
  rm -f tfplan

  echo
  echo "================================================================="
  echo "  APPLY SUMMARY for $module"
  echo "================================================================="
  echo
  strip_ansi < "$log" | grep -E "^Apply complete!|^Resources:" | head -2 \
    || echo "  (no Apply complete line)"
  echo
  echo "-- Outputs:"
  terraform output 2>&1 | head -30
}

# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------

cmd_verify() {
  cd "$module_dir"
  echo "==> tf.sh verify $module"
  echo
  echo "-- terraform output:"
  terraform output 2>&1 | head -50
  echo
  echo "-- Backend state key:"
  grep -E "^\s*key\s*=" providers.tf | head -1 || echo "  (no key found in providers.tf)"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "$cmd" in
  plan)   cmd_plan ;;
  apply)  cmd_apply ;;
  verify) cmd_verify ;;
  *)
    echo "Usage: $0 {plan|apply|verify} <module>" >&2
    exit 2
    ;;
esac
