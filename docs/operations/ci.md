# CI/CD operations

How Panakoes's CI/CD is wired together, why each piece exists, and what to do when it misbehaves. This is operational memory in repository form: hard-earned lessons that should outlive any single contributor's mental model.

---

## TL;DR

- Every PR runs **6 required status checks**: Terraform CI, gitleaks, CodeQL, CHANGELOG check, Python tests gate, TypeScript tests gate.
- Cascade rebases are automatic on every push to `main` via `auto-update-prs.yml`, authenticated by a fine-grained PAT (`AUTO_UPDATE_PAT`) so the cascade itself fires CI on the rebased SHA.
- Every CI workflow is `concurrency: cancel-in-progress` so rapid PR updates don't gridlock the runner pool.
- Auto-merge is the standard PR-landing path: arm with `gh pr merge <N> --squash --auto --delete-branch`. The PR lands when all required checks turn green.
- Local pre-flight: `make ci-pr` mirrors the relevant subset of remote CI in seconds. The pre-push git hook (`make install-hooks`) runs it automatically.

---

## Required status checks

Configured at the repository ruleset level. To see the current list:

```bash
gh api repos/Aztec03hub/panakoes/rulesets \
  | jq '.[] | select(.name == "main protection") | .id' \
  | xargs -I{} gh api repos/Aztec03hub/panakoes/rulesets/{} \
  | jq '.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks[].context'
```

The current required set:

| Check | Workflow | What it verifies |
|---|---|---|
| `Terraform fmt, validate, plan` | `terraform-ci.yml` | All Terraform modules format-clean, validate-clean, and plan without unintended drift. |
| `Scan for secrets` | `gitleaks.yml` | No secrets accidentally committed to the diff. |
| `Analyze (actions)` | `codeql.yml` | CodeQL static analysis on the GitHub Actions surface. |
| `Verify CHANGELOG.md updated when code changes` | `changelog-check.yml` | Source code changes ship with a CHANGELOG entry (or skip via the `skip-changelog` label for genuinely doc-only PRs). Dependabot PRs auto-skip via author check. |
| `Python tests gate` | `pytest.yml` | All Python services in the matrix pass `ruff check && mypy src && pytest`. The gate uses `if: always()` so it emits a SUCCESS even when no Python services changed. |
| `TypeScript tests gate` | `vitest.yml` | All TypeScript services in the matrix pass `pnpm biome check && pnpm typecheck && pnpm vitest run`. Same `if: always()` sentinel pattern as Python. |

**Why all gates use the always-runs sentinel pattern:** a required check that is path-filtered out and never emits a status causes the PR to lock up forever. The matrix workflows have a final aggregation job (`pytest-status`, `vitest-status`) with `if: always()` that emits SUCCESS when the matrix is empty (no relevant services changed). Required checks must always emit; design accordingly.

**Branch up-to-date enforcement:** `strict_required_status_checks_policy: true` is set, meaning a PR must be rebased on the latest `main` before merge. The `auto-update-prs` workflow handles that automatically; manual rebase is the fallback.

---

## Auto-update PR branches (`auto-update-prs.yml`)

When `main` moves, every open PR becomes "behind." Without intervention, each PR sits BLOCKED until manually rebased. The `auto-update-prs` workflow eliminates that toil: on every push to `main`, it iterates open PRs and calls the `update-branch` API to server-side-merge `main` into each.

### The PAT requirement (don't undo this)

The workflow uses **`secrets.AUTO_UPDATE_PAT`** rather than `secrets.GITHUB_TOKEN`. Per GitHub's anti-recursion policy, pushes authored by `GITHUB_TOKEN` do **NOT** trigger workflow runs. With `GITHUB_TOKEN`, the cascade silently invalidated every open PR's CI on a new SHA with no checks, gridlocking the queue. We hit this and burned hours on 2026-05-08; do not "simplify" the workflow back to `GITHUB_TOKEN`.

### PAT rotation

`AUTO_UPDATE_PAT` is a fine-grained PAT with:
- Repository access: `Aztec03hub/panakoes` only
- Permissions: Contents = Read+Write, Pull requests = Read+Write
- Expiration: 90 days

When the PAT expires, the cascade silently breaks. Symptoms: open PRs go BEHIND main and stay there; running `gh run list --workflow=auto-update-prs.yml` shows recent runs in `failure` state with 401 errors. Rotation steps:

1. https://github.com/settings/personal-access-tokens , create a new fine-grained PAT with the same scopes.
2. Repository settings → Secrets and variables → Actions → update `AUTO_UPDATE_PAT`.
3. Calendar reminder for the next rotation.

If the PAT is rotated outside the 90-day window, retroactively trigger an update by pushing a no-op commit to `main` (or wait for the next real merge).

### Long-term migration to GitHub App

For multi-developer or production-critical use, replace the PAT with a GitHub App's installation token. Apps offer no-expiration auto-rotating tokens, identity-as-service audit trails, and survival across maintainer transitions. Setup: https://github.com/settings/apps → create app → install on the repo → workflow uses `actions/create-github-app-token@v1` to mint a token at runtime. Skipped today because PAT is faster for a solo OSS project.

---

## Concurrency: cancel-in-progress (don't undo this either)

Every CI workflow has a `concurrency:` block:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

This groups runs by `(workflow, PR-or-branch)`. When a new event in the same group arrives, the prior run is cancelled, freeing the runner slot.

**Why:** rapid PR activity (close+reopen, force-push rebases, cascade updates) without this setting causes runs to pile up in `queued` status holding runner slots. We saturated the GitHub-hosted runner pool on 2026-05-08 with 130+ queued jobs; concurrency cancellation is what prevents that recurrence.

**Workflows intentionally without concurrency cancellation:**
- `auto-update-prs.yml`: has its own concurrency group (`auto-update-prs`) so cascades don't pile up but don't cancel mid-run either.
- `release.yml`: a release workflow cancelled mid-run could leave releases half-published.
- `scorecard.yml`: scheduled supply-chain scans are valuable in their entirety; cancelling halfway corrupts results.

---

## Dependabot grouping

Per `.github/dependabot.yml`, every service ecosystem groups **minor + patch** updates into a single weekly PR; major version bumps come as individual PRs.

**Why grouping matters for lockfile-based ecosystems** (npm, pnpm, pip+uv, Cargo): two parallel single-dep PRs both regenerate the lockfile against their own pre-merge view of `main`. The lockfile is a derived artifact, not source; there is no semantically correct three-way text merge of two parallel rewrites. Without grouping, every weekly Dependabot scan dumps N parallel PRs all fighting over the same lockfile, requiring `@dependabot recreate` on the survivors. With grouping, one PR per ecosystem per service per week, no contention.

**When you hit a stale Dependabot PR with a lockfile conflict:** comment `@dependabot recreate` (NOT `@dependabot rebase`; recreate forces a fresh lockfile against current `main`).

---

## Bypass actors

The branch-protection ruleset has one `bypass_actors`: the repo's `admin` role. This lets the maintainer (or anyone explicitly granted admin) `gh pr merge --admin` past required checks during incidents (CI infrastructure broken, urgent revert needed).

**This is an emergency tool, not a convenience.** Today's hard-earned lesson: admin-merging major version dep bumps without watching CI complete first cost us several hours of cleanup. The discipline:
- Doc-only / config-tightening: admin bypass is fine.
- Anything that touches code or major dep version: let real CI run. If it fails, that's the signal you needed.

---

## Local CI mirror (`make ci-pr`, `make ci-local`)

`make ci-pr` runs only the gates relevant to files changed against `origin/main`. Classifies your diff into Python services, TS services, Terraform modules, scripts, workflows, and runs the matching subset. Typical: 5-30 seconds.

`make ci-local` runs the full sweep: pre-commit + Python + TypeScript + Terraform across every service. Typical: 3-5 minutes.

The pre-push git hook (`make install-hooks` to install) prefers `ci-pr` (fast path) and falls back to `ci-local`. To bypass: `NO_VERIFY=1 git push`. Use bypass only when you've validated some other way; don't make a habit.

---

## Common failure modes and fixes

### "GitHub Actions queue stuck at N+ queued for 30+ minutes"

Most likely the runner pool is saturated. Cancel stale runs:

```bash
gh run list --limit 80 --json databaseId,status \
  --jq '.[] | select(.status == "queued" or .status == "in_progress" or .status == "pending") | .databaseId' \
  | xargs -I{} gh run cancel {}
```

Concurrency cancellation should prevent this from recurring after the cleanup. If it keeps happening, check whether something is creating runs faster than they can complete.

### "All my PRs are showing CI=no-checks even though I just pushed"

If the `auto-update-prs` cascade is still using `GITHUB_TOKEN` instead of the PAT, this happens. Verify:

```bash
grep -A2 'env:' .github/workflows/auto-update-prs.yml
```

You should see `GH_TOKEN: ${{ secrets.AUTO_UPDATE_PAT }}`. If you see `GITHUB_TOKEN`, that's the bug , change it back.

### "Dependabot PRs all DIRTY/CONFLICTING on pnpm-lock.yaml"

Comment `@dependabot recreate` on each. If the wave is large (more than 5 PRs in conflict), check whether `.github/dependabot.yml` is missing service entries or grouping config; the structural fix is grouping the minor+patch updates so they arrive as one PR instead of N.

### "TypeScript test gate failing with `pnpm tsc --noEmit` errors on services/admin"

Admin is a SvelteKit project. SvelteKit generates `.svelte-kit/tsconfig.json` at build time, and admin's `tsconfig.json` extends it. Plain `tsc --noEmit` fails because the file doesn't exist. The workflow runs `pnpm typecheck` per service, which in admin resolves to `svelte-kit sync && svelte-check ...` and creates the generated file before checking. If a TS service is added with a different shape, define a `typecheck` script in its `package.json` that does whatever pre-step is needed before tsc.

### "Required check Y is showing skipped/failed and PR can't merge"

Check the workflow's gate-job `if: always()` is working: open the workflow file and confirm the final aggregation job runs even when the matrix is empty. If a required check is path-filtered and the path isn't matched, the check never emits and the PR locks. Either:
- Drop the path filter so the workflow always fires (and let internal `if: always()` shortcut to a SUCCESS), or
- Apply a label workaround (e.g., `skip-changelog` for the CHANGELOG check) if the gate has one.

### "I need to merge a PR right now but CI is broken"

In order of preference:
1. **Fix CI.** Almost always the right call. Understand the failure, ship the fix, watch tests pass.
2. **Apply a skip label** if the failing gate has one (`skip-changelog`, etc.).
3. **`gh pr merge <N> --squash --admin`** as a last resort. Document the bypass in the commit message; ticket the underlying issue. Don't forget that `gh` itself needs `workflow` scope if the PR touches `.github/workflows/*`; without it, fall back to the GitHub UI's "Merge" button (web session has full scope).

### "I need to push directly to main"

Don't, except for explicit emergencies (the bypass actor exists for a reason but should not be daily-driven). If you must:
- SSH push bypasses OAuth scope issues with workflow files.
- Document the push reason in the commit message.
- Open a follow-up issue to land the change properly.

---

## Workflow file map

| File | Purpose | Trigger |
|---|---|---|
| `pytest.yml` | Python service tests + ruff + mypy | `pull_request`, `push:main`, `merge_group` |
| `vitest.yml` | TS service tests + biome + tsc | `pull_request`, `push:main`, `merge_group` |
| `terraform-ci.yml` | TF fmt + validate + plan | `pull_request`, `push:main`, `merge_group` |
| `gitleaks.yml` | Secret scanning | `pull_request`, `push:main`, `merge_group` |
| `codeql.yml` | Static analysis (Actions surface) | `pull_request`, `push:main`, weekly schedule, `merge_group` |
| `changelog-check.yml` | CHANGELOG entry gate | `pull_request`, `merge_group` (passes trivially) |
| `license-check.yml` | GPL-family rejection | `pull_request`, `merge_group` |
| `trivy.yml` | Vuln + IaC misconfig scan | `pull_request`, `merge_group` |
| `scorecard.yml` | OpenSSF Scorecard | `push:main`, weekly schedule |
| `actionlint.yml` | Workflow file lint | `pull_request`, `push:main`, `merge_group` (paths-filtered) |
| `lint-pr-title.yml` | Conventional Commit title | `pull_request_target` |
| `auto-update-prs.yml` | Cascade rebase open PRs | `push:main` |
| `release.yml` | Tag-driven GitHub releases | tag push |

---

## Maintenance cadence

- **Weekly (automatic):** Dependabot scan produces grouped minor+patch PRs Monday morning.
- **Quarterly (manual):** `pre-commit autoupdate` to refresh hook versions; verify ruleset settings still match this doc; rotate PAT if expiration is within 30 days.
- **Annually (manual):** review whether to migrate `auto-update-prs` from PAT to a GitHub App; review whether to enable GitHub merge queue (`merge_group` triggers are already wired across PR-firing workflows).

---

## Related documents

- [`CONTRIBUTING.md`](../../CONTRIBUTING.md) , developer environment + PR workflow.
- [`SECURITY.md`](../../SECURITY.md) , security posture (CI is part of the supply-chain story).
- [`.github/dependabot.yml`](../../.github/dependabot.yml) , Dependabot configuration.
- [`Makefile`](../../Makefile) , `make ci-pr`, `make ci-local`, `make pr-status`, `make install-hooks`.
- [`.githooks/pre-push`](../../.githooks/pre-push) , the local pre-push hook.
