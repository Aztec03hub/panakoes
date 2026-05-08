# ADR-030: Admin bypass actor on the branch-protection ruleset

## Status

Accepted. Lived since 2026-05-08.

## Context

Panakoes uses GitHub's modern repository ruleset API (not the older branch-protection-rule API) to enforce discipline on the `main` branch: required pull requests, six required status checks, linear history, no force-push, no deletion. The ruleset's strictness is intentional and well-aligned with the project's portfolio-quality goals.

Strict rulesets produce a known operational hazard: when CI infrastructure itself breaks, no PR can land until the infrastructure is fixed, even when the substantive change is correct and the operator has already verified it some other way. We hit several flavors of this on 2026-05-08:

- The cascade-rebase bug (ADR-028) caused PRs to silently lose CI. Fixing that bug required a PR to land. The PR could not land because the cascade was broken.
- The runner-pool exhaustion (ADR-027) left required checks queued for thirty-plus minutes. Fixing the underlying queue depth required a workflow-file PR. That PR could not land because the queue was full.
- Major-version dep bumps with Test failures required a fix-forward PR. The fix-forward PR couldn't merge until the original test failure was addressed.

In each case the maintainer needed an emergency bypass: a way to push a fix-forward change to `main` without waiting for the broken CI infrastructure that the fix-forward was repairing. Without a bypass, the repository can deadlock itself.

Originally the ruleset's `bypass_actors` array was empty. `gh pr merge --admin` returned "Repository rule violations found" because admin without a configured bypass actor is a no-op. The maintainer's only path forward during incidents was to push directly to `main` via SSH (which bypasses OAuth scope checks), an even less restrained workaround that left no audit signal that a bypass had occurred.

## Decision

The ruleset includes one bypass actor: the repository's `admin` role (actor_id 5, actor_type RepositoryRole), with `bypass_mode: always`.

This grants the maintainer (and anyone explicitly granted admin to the repo) the ability to `gh pr merge --admin` past the ruleset during incidents. The bypass is logged in the PR's audit trail (the merge event records that the bypass was used), so the post-incident review surface is preserved.

The bypass is for emergencies, not convenience. The discipline rule, documented in `docs/operations/ci.md`, is:

- Doc-only or config-tightening changes: admin bypass acceptable when CI is otherwise green and the maintainer has reviewed the diff.
- Anything that touches code or major dep version: real CI must run. The bypass is not a shortcut around test verification. Admin-merging a major dep bump without watching tests pass is exactly what produced the ADR-027 and ADR-029 incidents on the day this ADR was written.

The bypass does not weaken the ruleset for other contributors. Non-admin users hit the full ruleset. The bypass is scoped to the maintainer-as-admin role specifically.

## Consequences

**Positive:**

- The repository can recover from CI-infrastructure incidents without resorting to direct-push-to-main. The audit trail records each admin bypass; future post-incident review can identify which bypasses were used and whether they were warranted.
- The ruleset's strictness can stay strict. We do not need to weaken `strict_required_status_checks_policy` or remove required checks during normal operation just to leave room for incident recovery.
- Solo OSS reality is acknowledged: there is one maintainer, the same person is both author and reviewer, and the bypass is the one acceptable answer when "everything is broken and the only person who can fix it is the same person who needs to merge the fix."

**Negative:**

- Admin bypass is a security trade-off. A compromised maintainer account, or anyone elevated to admin, can land code without CI validation. The mitigation is the same as the rest of the project's threat model: the maintainer's GitHub account uses MFA, push protection is enabled, and admin access is granted only to the maintainer.
- Admin bypass is a discipline trap. The convenience of "just bypass it" is real and tempting, especially under time pressure. The 2026-05-08 incident produced concrete evidence of the cost: admin-merging major dep bumps without CI validation cost more time in cleanup than the CI wait would have cost in the first place. The discipline rule (above) is in the runbook precisely because the rule will be hard to follow in the moment.
- For multi-developer or production-team use, this configuration would need rethinking. A single admin role bypass is fine for solo OSS; for a team of contributors, the bypass should be scoped to a specific named person (or to a Break-Glass team) and audit-reviewed routinely. This ADR is scoped to the solo-OSS phase of the project.

## References

- `docs/operations/ci.md`, "Bypass actors" section.
- `feedback_panakoes_lessons.md` memory entry, "ruleset bypass actor configuration" section.
- 2026-05-08 incident set: cascade thrash (ADR-028), runner exhaustion (ADR-027), Dependabot lockfile conflicts (ADR-029), major-version dep bumps without CI verification.
- GitHub documentation: [About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets) and [Managing bypass permissions](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/managing-rulesets-for-a-repository#granting-bypass-permissions-for-a-ruleset).
