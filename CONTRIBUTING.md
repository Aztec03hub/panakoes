# CONTRIBUTING.md

Thanks for considering a contribution to Panakoes. This document covers the developer environment, branch and commit conventions, PR workflow, and the discipline rules that apply to everyone.

For project conventions used by Claude Code agents working on this repo, see [`CLAUDE.md`](CLAUDE.md).

---

## Developer Environment

### Prerequisites

- Linux or macOS (Windows via WSL2 supported and used by primary maintainer)
- Python 3.12+
- Node.js 22+ with `pnpm` 11+ package manager (pnpm 11.0.8 is pinned via `packageManager` in each TS service's `package.json`)
- Docker and Docker Compose
- Terraform 1.7+
- AWS CLI v2
- `gitleaks` (installed via pre-commit hook)
- `gh` (GitHub CLI) for PR workflows

### Initial Setup

```bash
# Clone the repo
git clone https://github.com/<owner>/panakoes.git
cd panakoes

# Python services: install uv (or use poetry)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Python dependencies for a specific service
cd services/<service-name>
uv sync

# TypeScript services and frontend: pnpm
pnpm install

# Pre-commit hooks (runs gitleaks, formatters, linters)
pip install pre-commit
pre-commit install

# Repo-managed git hooks (pre-push runs `make ci-pr` to mirror CI locally
# before every push; saves remote CI cycles when something is broken).
make install-hooks

# Terraform setup
cd infra
terraform init
```

### Local CI mirror

Running `make ci-pr` mirrors the relevant subset of remote CI against your changed files (`git diff` vs `origin/main`). Catches in seconds what remote CI catches in minutes.

```bash
make ci-pr        # focused: only gates whose inputs changed
make ci-local     # full sweep: pre-commit + Python + TypeScript + Terraform
```

Scope rules for `make ci-pr`:

- `pre-commit` runs every push (em-dash check, gitleaks, terraform fmt/validate, actionlint, EOF/whitespace). Always fast (<60s).
- For each Python service (`services/<name>/` with a `pyproject.toml`):
  - `ruff check` and `mypy` run when ANY file in the service dir changed (Dockerfile, .py, README, config). Fast (<30s/svc).
  - `pytest` runs ONLY when actual Python sources (`*.py`), `pyproject.toml`, `uv.lock`, or `conftest.py` changed. A Dockerfile-only or README-only change does NOT trigger pytest; Dockerfile shape is verified at `docker build` time on remote CI.
- The pytest phase is hard-budgeted at 8 minutes wallclock across all services combined so the pre-push hook stays under github.com's ~15-min SSH idle-timeout. Override locally with `CI_PR_PYTEST_TIMEOUT=N make ci-pr` (e.g. `CI_PR_PYTEST_TIMEOUT=1800` for 30 min). If the budget trips, ci-pr exits non-zero with a clear message; rely on server-side CI for full validation or bypass with `NO_VERIFY=1`.
- Infra-only / docs-only / Dockerfile-only diffs skip the pytest phase entirely (target: <60s end-to-end).

The pre-push hook installed by `make install-hooks` runs `make ci-pr` automatically before every `git push`. To bypass in an emergency: `NO_VERIFY=1 git push`. (Don't make a habit of it; the bypass exists for cases where you've already validated some other way.)

### Quick PR queue digest

`make pr-status` prints a one-line-per-PR view of every open PR's queue state (mergeability, CI verdict, auto-merge armed, labels, title). Useful when juggling multiple PRs in flight.

### AWS Credentials for Local Development

Local dev uses your personal AWS credentials via `aws configure sso` or `aws configure` profile. Do NOT commit AWS credential files. The `.gitignore` blocks `~/.aws/` patterns from leaking, but verify locally that `git status` doesn't show credential files before committing.

For CI/CD, AWS access happens via GitHub Actions OIDC federation. There are no long-lived AWS access keys anywhere.

---

## Branch and Commit Conventions

### Branching

- `main` is protected and always deployable. Direct pushes are blocked.
- All work happens on feature branches off `main`.
- Branch naming: `<type>/<short-description>` where `<type>` is one of `feat`, `fix`, `chore`, `docs`, `security`, `ci`, `refactor`, `test`, `perf`, `build`.
- Examples: `feat/streaming-websocket`, `fix/stripe-webhook-idempotency`, `docs/architecture-diagram`, `security/oidc-federation`.

### Commits

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

Format: `<type>(<scope>): <subject>`

Examples:
- `feat(transcription): add streaming WebSocket endpoint`
- `fix(billing): correct idempotency key handling for Stripe webhooks`
- `docs(architecture): add system data-flow diagram`
- `security(auth): require step-up MFA on admin lifecycle endpoints`
- `refactor(query-api): extract pagination helper`

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`, `build`, `security`.

The body of the commit (optional) explains the *why*, not the *what*. The diff already shows the *what*.

---

## Pull Request Workflow

1. **Branch from `main`.** Use the branch naming convention above.
2. **Write the test first** if your change is business logic, security path, or bugfix (TDD).
3. **Make focused commits** following Conventional Commits format. Commit early and often on the branch; commits get squashed at merge.
4. **Update `CHANGELOG.md`** under the `[Unreleased]` section in the appropriate category (Added, Changed, Deprecated, Removed, Fixed, Security). PR will fail CI if source code changed but CHANGELOG didn't (skippable for `docs:` / `chore:` PRs via label).
5. **Update `README.md`** if your change affects setup, tech stack, top-level service list, or breaking architectural shape.
6. **Run tests locally:** `make test` (or the relevant service-specific command).
7. **Run lint and type-check locally:** `make lint`.
8. **Push the branch and open a PR** via `gh pr create` or the GitHub UI.
9. **Fill out the PR template:** summary, change type, testing notes, CHANGELOG entry checkbox.
10. **Wait for CI to pass.** Required checks: tests, lint, gitleaks, CodeQL, Terraform plan (if infra touched), CHANGELOG-updated.
11. **Self-review the diff** in the GitHub PR view. You'd be surprised how often a fresh look catches things.
12. **Squash-and-merge** to `main` once green. The squashed commit message follows Conventional Commits format and serves as the changelog entry source.
13. **Delete the branch** after merge.

### Releases and Tagging

- We use [SemVer](https://semver.org/): `vMAJOR.MINOR.PATCH`.
- MAJOR for breaking changes, MINOR for new features, PATCH for fixes.
- Tagging is performed on `main` after the desired commits are merged: `git tag -a v0.1.0 -m "v0.1.0: initial release"`.
- Pushing a tag triggers a GitHub Release with auto-generated notes from PRs since the prior tag, and cuts the `[Unreleased]` section in CHANGELOG.md into a versioned section.

---

## Discipline Rules

These are non-negotiable and apply to humans and AI agents equally:

1. **No secrets in source code, ever.** Read `.env.example` for the env var contract; production values come from AWS Secrets Manager or SSM Parameter Store at runtime.
2. **No em-dashes** in any project content (commit messages, doc copy, code comments, marketing). Use commas, periods, parentheses, semicolons. (Hard rule from project maintainer.)
3. **Conventional Commits format** for every commit.
4. **CHANGELOG.md updated** for every meaningful change.
5. **README.md updated** when affected.
6. **Test-first** for business logic, security paths, and bugfixes.
7. **80% coverage minimum** on services, **100% on auth/billing/audit paths**, **70% on infra-adjacent code**. CI fails the PR below thresholds.
8. **No force-push to `main`**, ever, except a documented secret-scrub emergency.
9. **No `git reset --hard` on shared history.** Rollback via `git revert`.

---

## Reporting Issues

For non-security bugs and feature requests, use GitHub Issues. Templates exist for both. Provide reproduction steps, expected vs actual behavior, environment details.

For security vulnerabilities, see [`SECURITY.md`](SECURITY.md).

---

## Code of Conduct

Be excellent to each other. Direct, professional, candid. Disagreement is welcome; disrespect is not. The maintainer reserves the right to remove participants whose conduct undermines the collaborative environment.

---

## Questions

Open a GitHub Discussion or email plafaydev@gmail.com.
