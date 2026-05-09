# ADR-029: Dependabot grouped minor+patch updates per service

## Status

Accepted. Lived since 2026-05-08.

## Context

Panakoes is a polyglot monorepo with sixteen services across multiple package ecosystems: Python (uv-managed pyproject.toml), TypeScript (pnpm-managed pnpm-lock.yaml), Terraform providers, GitHub Actions versions. We rely on Dependabot to surface dependency updates with security advisories first, then minor and patch bumps weekly.

Originally each service entry in `.github/dependabot.yml` had no `groups:` configuration, meaning Dependabot opened one PR per dep update. With ten or more dep updates pending across services on a given Monday, that means ten or more parallel PRs all rewriting the same lockfile (`pnpm-lock.yaml` per TS service, `uv.lock` per Python service).

Lockfiles are derived artifacts, not source code. There is no semantically correct three-way text merge of two parallel lockfile rewrites. Each PR's lockfile is regenerated against the PR's own pre-merge view of `main`; when the first PR lands, the second's lockfile no longer matches the new `main` state. Git treats the resulting overlap as a content conflict that requires manual resolution, but the resolution is not "edit the conflict markers" (the lockfile's structure is too complex for hand-editing), it's "regenerate the lockfile from a fresh `pnpm install` against the merged package.json." That regeneration is exactly what `@dependabot recreate` does, but it has to be invoked PR-by-PR, sequentially, and Dependabot is not infinitely fast at processing these comments.

We hit this in production form on 2026-05-08. After expanding `.github/dependabot.yml` to cover thirteen previously-uncovered services, Dependabot scanned and produced seventeen PRs in one wave. Multiple PRs per service all conflicted on the same lockfile; resolving them required hours of `@dependabot recreate` ping-pong. We eventually merged most via manual sed-edit-package-json + pnpm-install + force-push because Dependabot's recreate path could not keep up.

The diagnosis was clear: the failure mode is structural to "many parallel single-dep PRs against a shared lockfile." The fix has to be structural: stop generating parallel PRs.

## Decision

Every per-service entry in `.github/dependabot.yml` declares a `groups:` configuration that bundles all minor and patch updates into a single weekly PR per service per ecosystem:

```yaml
groups:
  <service-slug>-minor-patch:
    update-types:
      - "minor"
      - "patch"
```

Major version bumps stay UNGROUPED. Each major version of a dependency arrives as its own PR so each can be reviewed individually for breaking changes, run through CI in isolation, and reverted individually if it fails tests. This is by design: major bumps are exactly where breaking changes hide, and the PR-per-major model ensures every breaking change has a dedicated CI run to validate against.

The schedule across all entries is unified to weekly Monday so a single Dependabot scan absorbs the week's output. Same-day arrival means a single CI cycle handles them, rather than trickling in throughout the week.

Coverage is also expanded: every service under `services/` that ships its own `pyproject.toml` or `package.json` has a corresponding update entry. Prior to 2026-05-08 the config only covered four of sixteen services; the rest received zero Dependabot oversight, a real security-posture gap that this ADR closes.

## Consequences

**Positive:**

- Lockfile contention is structurally eliminated for minor and patch updates. The grouped PR rewrites the lockfile once per service per week; no parallel-rewrite class of conflict exists for that PR class.
- Major version bumps retain their individual-PR review path. The most dangerous class of update (semantic-version-major-implies-breaking-change) is exactly the class where the more conservative individual-PR workflow is right.
- Coverage is complete: every service with a manifest is monitored. Security advisories that previously only landed for four services now land for all sixteen.
- The weekly cadence is predictable. Mondays are "Dependabot day"; the rest of the week is feature work without Dependabot churn.

**Negative:**

- A failure in any minor/patch dep within a group causes the entire grouped PR to fail CI and not auto-merge. The group becomes one "all-or-nothing" landing event. Mitigated by Dependabot's `@dependabot recreate` (regenerates the group with current main) and by the option to manually split a problematic dep out into its own PR.
- A grouped PR has a larger diff than a single-dep PR, which makes review marginally harder. Acceptable for minor and patch bumps which are by SemVer contract non-breaking; review can be cursory. (For major bumps, where review matters, the individual PR is preserved.)
- New services added to the repo require a corresponding `dependabot.yml` entry. Easy to forget. Mitigated by adding it to the new-service onboarding checklist (see `services/_template/README.md`).

## References

- `.github/dependabot.yml`, the canonical configuration.
- `docs/operations/ci.md`, "Dependabot grouping" section.
- 2026-05-08 incident: 17 PRs from a single scan, lockfile contention, multi-hour recovery.
- `feedback_panakoes_lessons.md` memory entry, "Dependabot lockfile conflicts" section.
- GitHub documentation: [Dependabot grouped updates](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file#groups).
