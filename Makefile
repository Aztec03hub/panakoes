.PHONY: help setup test test-unit test-integration lint typecheck coverage check clean dev-up dev-down dev-rebuild gpr

# Find every Python service in services/ that has a pyproject.toml
PY_SERVICES := $(shell find services -maxdepth 2 -name pyproject.toml -exec dirname {} \;)

help:
	@echo "Common targets:"
	@echo "  setup         Install dev deps for all Python services"
	@echo "  test          Run all tests across all services"
	@echo "  test-unit     Run only unit tests"
	@echo "  test-integration  Run only integration tests"
	@echo "  lint          Run ruff across all services"
	@echo "  typecheck     Run mypy across all services"
	@echo "  coverage      Generate coverage report"
	@echo "  check         Run lint + typecheck + tests (mirrors CI)"
	@echo "  clean         Remove all build/cache artifacts"
	@echo ""
	@echo "Local dev stack:"
	@echo "  dev-up        Start postgres + dynamodb-local (DEV_LOCALSTACK=1 also starts localstack)"
	@echo "  dev-down      Stop the dev stack"
	@echo "  dev-rebuild   Rebuild compose images with --no-cache"
	@echo ""
	@echo "PR helpers:"
	@echo "  gpr PR=<n>    Rebase + force-push + auto-merge (alias for scripts/gpr-fix-merge.sh)"

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

# -----------------------------------------------------------------------------
# PR helpers.
# Usage: make gpr PR=123
# -----------------------------------------------------------------------------
gpr:
	@if [ -z "$(PR)" ]; then echo "Usage: make gpr PR=<number>" >&2; exit 64; fi
	@scripts/gpr-fix-merge.sh $(PR)
