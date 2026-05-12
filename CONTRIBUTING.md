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

# Repo-managed git hooks (pre-push runs `make ci-fast`, a sub-90-second
# focused gate. Server-side CI is the canonical merge gate; the hook is
# there to catch the silly stuff fast).
make install-hooks

# Terraform setup
cd infra
terraform init
```

### Local CI tiers

Server-side CI on GitHub Actions is the canonical merge gate. The local targets exist to catch issues fast, not to mirror every server-side check.

| Target | Wall-clock | What it runs | When to use |
|---|---|---|---|
| `make ci-fast` | <= 90s | gitleaks, em-dash detector, actionlint (changed workflows), terraform fmt -check (changed modules), ruff check (changed .py only) | Default pre-push gate (runs automatically) |
| `make ci-pr` | 1-8 min | Scope-narrowed mirror of remote CI: pre-commit, ruff + mypy + pytest for changed Python services, biome + typecheck + vitest for changed TS services, terraform fmt + validate for changed modules | Manual; before tagging a release; when in doubt |
| `make ci-full` | 1-8 min | Alias for `make ci-pr` (clearer naming going forward) | Same as ci-pr |
| `make ci-local` | 5-15 min | Full sweep: pre-commit on every file + every Python service + every TS service + every Terraform module | Rare; weekly hygiene sweep |

The pre-push hook installed by `make install-hooks` runs `make ci-fast` automatically before every `git push`. The hook is hard-bounded at 120 seconds (override with `_PREPUSH_TIMEOUT_S=N`); if a check exceeds that budget it has no business living in the pre-push hook (push it server-side instead).

Escape hatches:

- `NO_VERIFY=1 git push` skips the hook entirely. Documented bypass; use sparingly. Every NO_VERIFY=1 push is a workflow-fix trigger (the hook should not be in your way; if it is, fix the hook).
- `CI_FULL=1 git push` runs `make ci-full` (the heavier ci-pr behavior) instead of ci-fast. Use before tagging a release or when you genuinely want the full local sweep.
- `CI_FULL=1 make ci-fast` is equivalent to `make ci-full` (the script delegates to ci-pr.sh).

What ci-fast intentionally does NOT run:

- `pytest` (server-side CI gates this)
- `vitest` (server-side CI gates this)
- `mypy --strict` (slow on cold cache; server-side gates this)
- `pre-commit run --all-files` (server-side gates this)
- `pnpm install` / `uv sync` (network + minutes; server-side handles dep install)

If a contributor finds themselves repeatedly running `NO_VERIFY=1 git push` because ci-fast is "in the way", that is a signal to lighten ci-fast further, not to normalize the bypass. Em-dashes leaked to main once already (PR #232 -> #242) through this exact failure mode; the fix is keeping ci-fast genuinely fast, not skipping it.

On any failure the hook prints the full log file path (under `$TMPDIR` or `/tmp`); inspect it with `less` or your editor of choice.

If `.githooks/` exists in the repo but you haven't run `make install-hooks` yet, `make ci-fast`, `make ci-pr`, and `make ci-local` print a one-line WARN reminding you to enable the hook. The reminder is soft; it never fails the build.

The hook tests live at [`tests/hooks/test_pre_push.sh`](tests/hooks/test_pre_push.sh). Run them via `bash tests/hooks/test_pre_push.sh`; they inject a fake `make` via `_PREPUSH_MAKE_BIN` and verify the NO_VERIFY short-circuit, non-zero propagation on failure, and the timeout-budget path.

### Container image bakes

Container images for every Panakoes service are baked on GitHub Actions, not locally. The canonical bake path is:

1. **Automatic on push to `main`**: `.github/workflows/image-bake-on-change.yml` detects which services changed (per-service path filter via `dorny/paths-filter`) and bakes only those, in parallel, multi-arch (linux/amd64 + linux/arm64), pushing to ECR via OIDC.
2. **Manual one-button bake**: trigger `.github/workflows/image-bake-manual.yml` from the Actions UI. Pick a service, optionally pin a tag, optionally tick `register-as-default` to auto-open a follow-up PR that bumps the ECS image_tag default. Useful for base-image CVE rotations or rebakes after a flaky push.
3. **Reusable workflow**: `.github/workflows/image-bake.yml` is `workflow_call`-only; both of the above call into it. It encapsulates the OIDC role assumption, buildx setup, and the build flags required to emit Docker Manifest V2 (not OCI) so the result is pullable by ECS, Lambda, and EKS without surprise. See `aws_lambda_container_image_gotchas.md` for the gotcha those flags defend against.

**Local `docker buildx` is a fallback for offline development only.** Two segfaults in two days on the maintainer's WSL2 host (Docker Desktop VHDX corruption) are the immediate trigger for moving to GHA; even without that, GHA bakes are reproducible, multi-arch by default, and auditable in the Actions log. Do not push locally-baked images to `panakoes-dev-*` ECR repos as part of normal workflow.

The OIDC role assumed by all three workflows is `arn:aws:iam::659225405128:role/panakoes-github-actions`, defined in `infra/global/main.tf` and scoped via the `token.actions.githubusercontent.com:sub` claim to `repo:<owner>/panakoes:*`. No long-lived AWS access keys exist in GitHub Secrets.

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
4. **Drop a `.changelog/` fragment** at `.changelog/<UTC-timestamp>-<short-slug>.md`. Generate the timestamp with `date -u +%Y%m%dT%H%M%SZ`. The file has YAML frontmatter (`category: Added|Changed|Deprecated|Removed|Fixed|Security`) and a terse user-visible Markdown bullet body; see [`.changelog/README.md`](.changelog/README.md) for the format and rationale. PR will fail CI if source code changed but no fragment was added (skippable for `docs:` / `chore:` PRs via label). Direct edits to `CHANGELOG.md` are still accepted as a fallback (e.g. backport typo fixes) but the fragment pattern is the canonical going-forward flow.
5. **Update `README.md`** if your change affects setup, tech stack, top-level service list, or breaking architectural shape.
6. **Run tests locally:** `make test` (or the relevant service-specific command).
7. **Run lint and type-check locally:** `make lint`.
8. **Push the branch and open a PR** via `gh pr create` or the GitHub UI.
9. **Fill out the PR template:** summary, change type, testing notes, CHANGELOG entry checkbox.
10. **Wait for CI to pass.** Required checks: tests, lint, gitleaks, CodeQL, Terraform plan (if infra touched), CHANGELOG-updated. PRs touching `infra/**` additionally trigger the `Terraform plan on PR` workflow, which posts a sticky per-module plan comment and fails the build if the plan would destroy or replace resources without the `replace-allowed` label on the PR (see `infra/README.md` for the full workflow). After any push to `main`, the `Auto-rebase open PRs` workflow sweeps every open PR and calls the GitHub Update Branch API so siblings refresh automatically; trust the bot rather than manually rebasing.
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
4. **Changelog fragment dropped** under `.changelog/` for every meaningful change (see step 4 of the PR workflow above).
5. **README.md updated** when affected.
6. **Test-first** for business logic, security paths, and bugfixes.
7. **80% coverage minimum** on services, **100% on auth/billing/audit paths**, **70% on infra-adjacent code**. CI fails the PR below thresholds.
8. **No force-push to `main`**, ever, except a documented secret-scrub emergency.
9. **No `git reset --hard` on shared history.** Rollback via `git revert`.

---

## Conventions

### JWT env-var naming: signers vs validators

The auth service signs JWTs; every other service validates them. The env-var prefixes differ on purpose, and confusing them has caused a real production-shaped bug (see PR #218 cost-api / admin-api and PR #223 ingestion-api / session-manager retrospectives).

- **Signers** (today: `services/auth`, the Better-Auth TypeScript service) read:
  - `AUTH_JWT_SECRET`
  - `AUTH_JWT_ISSUER`
  - `AUTH_JWT_AUDIENCE`
- **Validators** (every Python service that calls `panakoes_auth_client.from_env()`) read:
  - `JWT_SECRET`
  - `JWT_ISSUER`
  - `JWT_AUDIENCE`

Rationale: the validator contract lives in `services/auth-client/src/panakoes_auth_client/config.py` and is the single canonical source for every consuming Python service. Forcing every service to standardize on `JWT_*` means a brand-new validator can adopt the shared client with zero per-service env mapping. The signer keeps `AUTH_JWT_*` because Better-Auth library conventions use that naming and because keeping the two halves of the contract verbally distinct makes operator mistakes (wiring the signer's secret into a validator's env, or vice versa) catch at boot time rather than first-request time.

When adding a new Python service that validates JWTs:
1. Define `jwt_secret`, `jwt_issuer`, `jwt_audience` fields in its `pydantic-settings` `Settings` class (or call `from_env()` directly).
2. In its Terraform task definition, wire `JWT_SECRET` as a secret and `JWT_ISSUER` / `JWT_AUDIENCE` as plain env vars, mirroring `infra/dev/ecs/cost_api.tf` and `infra/dev/ecs/admin_api.tf`.
3. Add a unit test analogous to `services/<name>/tests/unit/test_config_env.py` that pins the env var contract; this protects against silent regressions.

If you find a Python service still reading `AUTH_JWT_SECRET` for validation, treat it as a bug and standardize it on `JWT_*` in the same PR.

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
