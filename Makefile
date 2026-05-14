.PHONY: help setup test test-unit test-integration lint typecheck coverage check clean dev-up dev-down dev-rebuild test-local gpr wait-pr install-hooks hooks-check ci-local ci-pr ci-fast ci-full pr-status pre-commit-all ts-check tf-check seed-admin
.PHONY: help setup test test-unit test-integration lint typecheck coverage check clean dev-up dev-down dev-rebuild test-local gpr wait-pr install-hooks hooks-check ci-local ci-pr pr-status pre-commit-all ts-check tf-check openapi-emit openapi-check seed-admin

# Services that emit a checked-in OpenAPI schema. Each must have a
# `scripts/emit-openapi.py` that writes `openapi.json` next to its
# `pyproject.toml`. Keep this list in lockstep with the actual
# emit-scripts on disk.
OPENAPI_SERVICES := services/cost-api services/admin-api

# Find every Python service in services/ that has a pyproject.toml
PY_SERVICES := $(shell find services -maxdepth 2 -name pyproject.toml -exec dirname {} \;)
# Find every TypeScript service in services/ that has a package.json
TS_SERVICES := $(shell find services -maxdepth 2 -name package.json -not -path '*/node_modules/*' -exec dirname {} \;)
# Find every Terraform module
TF_DIRS     := $(shell find infra -maxdepth 3 -name '*.tf' -exec dirname {} \; | sort -u)

help:
	@echo "Common targets:"
	@echo "  setup           Install dev deps for all Python services"
	@echo "  test            Run all tests across all services"
	@echo "  test-unit       Run only unit tests"
	@echo "  test-integration  Run only integration tests"
	@echo "  lint            Run ruff across all services"
	@echo "  typecheck       Run mypy across all services"
	@echo "  coverage        Generate coverage report"
	@echo "  check           Run lint + typecheck + tests (Python only)"
	@echo "  ci-fast         Sub-90s pre-push gate: gitleaks + em-dash + actionlint + tf fmt + ruff (changed files only)"
	@echo "  ci-pr           Scope-narrowed mirror of remote CI (pytest, vitest, biome, etc.). Slow on multi-service PRs."
	@echo "  ci-full         Alias for ci-pr (clearer naming going forward)"
	@echo "  openapi-emit    Regenerate checked-in openapi.json for each service"
	@echo "  openapi-check   Verify checked-in openapi.json matches the live FastAPI app (CI drift gate)"
	@echo "  ci-local        Full CI mirror: pre-commit + Python + TypeScript + Terraform"
	@echo "  pre-commit-all  Run every pre-commit hook on every file"
	@echo "  ts-check        biome + typecheck + vitest for every TS service"
	@echo "  tf-check        terraform fmt + validate for every module"
	@echo "  pr-status       One-line digest of every open PR's queue state"
	@echo "  clean           Remove all build/cache artifacts"
	@echo ""
	@echo "Local dev stack:"
	@echo "  dev-up          Start postgres + dynamodb-local (DEV_LOCALSTACK=1 also starts localstack)"
	@echo "  dev-down        Stop the dev stack"
	@echo "  dev-rebuild     Rebuild compose images with --no-cache"
	@echo "  test-local      Run all Python tests against the local stack (zero AWS cost)"
	@echo ""
	@echo "PR helpers:"
	@echo "  gpr PR=<n>            Rebase + force-push + auto-merge (alias for scripts/gpr-fix-merge.sh)"
	@echo "  wait-pr PRS=\"a b c\"   Wait for one or more PRs (--any/--all via MODE=, --auto-update via AUTO_UPDATE=1)"
	@echo ""
	@echo "Git hooks:"
	@echo "  install-hooks   Configure this clone to use .githooks/ (pre-push runs ci-fast)"
	@echo "  hooks-check     Soft-warn if .githooks/ exists but core.hooksPath is unset"
	@echo ""
	@echo "Operator helpers:"
	@echo "  seed-admin EMAIL=foo@example.com  Promote a user to admin role (requires AWS_PROFILE)"

setup:
	@for svc in $(PY_SERVICES); do \
		echo "==> Installing deps for $$svc"; \
		(cd $$svc && uv sync --group dev) || exit 1; \
	done

test:
	@for svc in $(PY_SERVICES); do \
		echo "==> Testing $$svc"; \
		(cd $$svc && uv run pytest) || exit 1; \
	done

test-unit:
	@for svc in $(PY_SERVICES); do \
		(cd $$svc && uv run pytest -m unit) || exit 1; \
	done

test-integration:
	@for svc in $(PY_SERVICES); do \
		(cd $$svc && uv run pytest -m integration) || exit 1; \
	done

lint:
	@for svc in $(PY_SERVICES); do \
		(cd $$svc && uv run ruff check) || exit 1; \
	done

typecheck:
	@for svc in $(PY_SERVICES); do \
		(cd $$svc && uv run mypy src) || exit 1; \
	done

coverage:
	@for svc in $(PY_SERVICES); do \
		(cd $$svc && uv run pytest --cov-report=html) || exit 1; \
	done

check: lint typecheck test

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache -o -name htmlcov -o -name .coverage \) -prune -exec rm -rf {} + 2>/dev/null || true

# -----------------------------------------------------------------------------
# Local dev stack (postgres, dynamodb-local, optional localstack).
# See scripts/dev-up.sh and docker-compose.yml for details.
# -----------------------------------------------------------------------------
dev-up:
	@scripts/dev-up.sh

dev-down:
	@docker compose down

dev-rebuild:
	@docker compose build --no-cache

test-local: ## Run all Python integration tests against the local dev stack (zero AWS cost)
	@echo "==> Starting local dev stack with LocalStack..."
	@DEV_LOCALSTACK=1 scripts/dev-up.sh
	@echo "==> Running all tests with local AWS endpoints..."
	@AWS_ENDPOINT_URL=http://localhost:4566 \
	  AWS_ACCESS_KEY_ID=test \
	  AWS_SECRET_ACCESS_KEY=test \
	  AWS_DEFAULT_REGION=us-east-1 \
	  DATABASE_URL=postgresql://panakoes:panakoes@localhost:5432/panakoes \
	  DDB_ENDPOINT_URL=http://localhost:8000 \
	  OTEL_SDK_DISABLED=true \
	  $(MAKE) test

# -----------------------------------------------------------------------------
# PR helpers.
# Usage: make gpr PR=123
# Usage: make wait-pr PRS="80 81 82" [MODE=any|all] [INTERVAL=15] [TIMEOUT=300] [AUTO_UPDATE=1]
# Usage: make wait-pr PR=123 [INTERVAL=15] [TIMEOUT=300]
# -----------------------------------------------------------------------------
gpr:
	@if [ -z "$(PR)" ]; then echo "Usage: make gpr PR=<number>" >&2; exit 64; fi
	@scripts/gpr-fix-merge.sh $(PR)

wait-pr:
	@if [ -z "$(PRS)$(PR)" ]; then echo "Usage: make wait-pr PRS=\"80 81\" [MODE=any|all] [INTERVAL=N] [TIMEOUT=N] [AUTO_UPDATE=1]" >&2; exit 64; fi
	@scripts/wait-for-pr.sh $(PRS) $(PR) \
		$(if $(MODE),--$(MODE)) \
		$(if $(INTERVAL),--interval $(INTERVAL)) \
		$(if $(TIMEOUT),--timeout $(TIMEOUT)) \
		$(if $(AUTO_UPDATE),--auto-update)

# -----------------------------------------------------------------------------
# Git hooks. Idempotent. Run once per clone (or after cloning fresh).
# -----------------------------------------------------------------------------
install-hooks:
	@scripts/install-githooks.sh

# hooks-check: one-line WARN if .githooks/ exists but this clone isn't
# configured to use it. Wired into ci-local and ci-pr so the prompt
# surfaces exactly when an operator is running local CI but has not yet
# run `make install-hooks`. Soft warning only; never fails the build.
hooks-check:
	@if [ -d .githooks ] && [ "$$(git config --get core.hooksPath 2>/dev/null)" != ".githooks" ]; then \
		echo "[hooks-check] WARN: .githooks/ exists but core.hooksPath is not set to .githooks; run 'make install-hooks' to enable the pre-push gate." >&2; \
	fi

# -----------------------------------------------------------------------------
# Mirrors-of-CI local gates. Run BEFORE pushing to catch what CI catches,
# without paying the 3-5 minute remote CI cycle.
#
# ci-local       Full sweep: pre-commit + Python + TypeScript + Terraform.
# pre-commit-all Run every pre-commit hook against every file.
# ts-check       biome + typecheck + vitest for each TS service.
# tf-check       terraform fmt -check + validate for each module.
#
# `check` (existing target) only covers Python services; ci-local is the
# full CI mirror. Use this as the last step before `git push`.
# -----------------------------------------------------------------------------
ci-local: hooks-check pre-commit-all check ts-check tf-check
	@echo "==> All local CI gates passed."

# ci-pr: the focused, fast cousin of ci-local. Looks at `git diff vs
# origin/main`, classifies changed files, and runs only the gates whose
# inputs actually changed. Use this in pre-push hooks and during
# day-to-day branch work to skip the 3-5 min full sweep when most of
# the codebase wasn't touched. Falls through to ci-local if you want
# the kitchen sink.
ci-pr: hooks-check
	@scripts/ci-pr.sh

# ci-fast: the sub-90-second pre-push gate. Replaces ci-pr as the default
# pre-push target. Catches the silly stuff fast (secrets, em-dashes,
# broken workflow YAML, unformatted Terraform, path-scoped ruff). Never
# runs pytest / vitest / mypy / full pre-commit-all -- those live
# server-side where wall-clock does not block the push. See
# feedback_ci_fast_pre_push.md for the design rationale.
ci-fast: hooks-check
	@scripts/ci-fast.sh

# ci-full: clearer-named alias for ci-pr (the slower "mirror remote CI"
# path). Use this when CI_FULL=1 is set on a push, before tagging a
# release, or when you genuinely want the heavy local sweep.
ci-full: ci-pr

# pr-status: one-line digest of every open PR's queue state. Replaces
# ad-hoc `gh pr list ... | jq` queries.
pr-status:
	@scripts/pr-status.sh

pre-commit-all:
	@echo "==> pre-commit run --all-files"
	@pre-commit run --all-files

ts-check:
	@for svc in $(TS_SERVICES); do \
		echo "==> TypeScript: $$svc"; \
		(cd $$svc && pnpm install --frozen-lockfile --prefer-offline >/dev/null 2>&1 && \
		 pnpm biome check && \
		 ( pnpm typecheck 2>/dev/null || pnpm tsc --noEmit ) && \
		 pnpm test 2>/dev/null) || exit 1; \
	done

tf-check:
	@for tfdir in $(TF_DIRS); do \
		echo "==> Terraform: $$tfdir"; \
		(cd $$tfdir && terraform fmt -check -diff && terraform validate -no-color) || \
		(cd $$tfdir && terraform init -backend=false -no-color >/dev/null && terraform validate -no-color) || \
		exit 1; \
	done

# OpenAPI schema emit + drift check. The emit script imports the live
# FastAPI app, writes app.openapi() to the service's checked-in
# openapi.json, and strips environment-specific server URLs. The
# check target re-runs emit and fails if `git diff` shows drift; CI
# uses this to gate PRs that change route shapes without updating
# the artifact downstream codegen reads from.
openapi-emit:
	@for svc in $(OPENAPI_SERVICES); do \
		echo "==> OpenAPI emit: $$svc"; \
		(cd $$svc && uv run python scripts/emit-openapi.py) || exit 1; \
	done

openapi-check: openapi-emit
	@echo "==> OpenAPI drift check"
	@if ! git diff --exit-code -- $(addsuffix /openapi.json,$(OPENAPI_SERVICES)); then \
		echo ""; \
		echo "ERROR: checked-in openapi.json is stale."; \
		echo "Run: make openapi-emit && git add services/*/openapi.json"; \
		exit 1; \
	fi

# -----------------------------------------------------------------------------
# Operator helpers.
# seed-admin: promote a user to role=admin via ECS exec against the auth task.
# Usage: make seed-admin EMAIL=foo@example.com
# Requires AWS_PROFILE in the environment (e.g. panakoes-admin).
# See docs/runbooks/seed-admin.md for prerequisites and troubleshooting.
# -----------------------------------------------------------------------------
seed-admin: ## Promote a user to admin role
	@if [ -z "$(EMAIL)" ]; then \
		echo "Usage: make seed-admin EMAIL=foo@example.com" >&2; \
		exit 64; \
	fi
	@EMAIL="$(EMAIL)" services/auth/scripts/seed-admin.sh
