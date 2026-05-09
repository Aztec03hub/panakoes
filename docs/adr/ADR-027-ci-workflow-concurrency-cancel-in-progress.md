# ADR-027: Concurrency cancel-in-progress on CI workflows

## Status

Accepted. Lived since 2026-05-08.

## Context

Panakoes runs nine CI workflows on every pull request: pytest, vitest, Terraform CI, gitleaks, CodeQL, license-check, Trivy, actionlint, CHANGELOG check. Six are required for merge. Each workflow's matrix expansion produces multiple jobs (one per Python service, one per TypeScript service, etc.), so a typical PR consumes roughly twenty to thirty individual jobs from the GitHub-hosted runner pool.

On 2026-05-08 we hit a queue-saturation incident. A cascade-rebase bug (see ADR-028) caused PRs to silently lose their CI status, prompting an operator close-and-reopen sweep across seventeen open PRs. Each reopen fired a fresh run set. Prior in-flight runs and their queued successors continued to hold runner slots until they aged out or completed naturally; the GitHub-hosted runner pool for free public repositories is shared and finite. Within a few minutes the actions queue depth was 130+ jobs. Workflows sat in `queued` status for thirty-plus minutes. Required gates never emitted. Auto-merge waited indefinitely on PRs that had nothing wrong with them other than starvation.

The runner pool is a shared resource we do not control. The behavior of a workflow under repeated rapid invocation is something we do control.

## Decision

Every CI workflow that fires on `pull_request` or `push` gets a `concurrency:` block configured to cancel in-progress runs in the same group when a new event arrives:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

The grouping key is `(workflow, PR-number-or-branch-ref)`. Different PRs run in independent groups; different workflows run in independent groups; pushes to `main` run in their own group keyed on the ref. When a new event in the same group arrives (a force-push to a PR, a close-and-reopen, a rebase that updates the PR head), the prior run is cancelled immediately, freeing the runner slot.

Workflows updated: pytest, vitest, license-check, gitleaks, codeql, actionlint, changelog-check, trivy. (Terraform CI already had concurrency from its initial PR.)

Workflows intentionally without `cancel-in-progress`:

- `auto-update-prs.yml` already had a singleton concurrency group (`auto-update-prs`) so cascade sweeps don't pile up; cancel-in-progress is unnecessary because the workflow is short and idempotent.
- `release.yml` is triggered by tag pushes and we do not want a new tag cancelling a publish in flight; releases must complete or fail explicitly.
- `scorecard.yml` runs on a weekly schedule; cancelling halfway corrupts the SARIF upload to GitHub code scanning.

## Consequences

**Positive:**

- Runner-pool exhaustion from rapid PR activity is structurally prevented. The `(workflow, PR)` group ensures each PR holds at most one running set of jobs at a time.
- Force-push iteration is faster: a developer pushing a fix to a PR no longer waits behind the prior run's slow tests.
- Cascade rebases (ADR-028) interact correctly with concurrency: when the cascade pushes a new SHA to a PR's branch, the PR's prior CI cancels and the new SHA's CI starts immediately.
- The runner-queue gridlock failure mode is documented in `docs/operations/ci.md` along with the diagnostic command (`gh run list --json status --jq '...'`) and recovery procedure (mass-cancel queued runs).

**Negative:**

- A run cancelled in-flight does not produce coverage data or test logs that might have been useful for debugging. Mitigated because the next run on the new SHA produces equivalent or better data.
- Workflows that have non-idempotent side effects (writing to external systems, posting comments, uploading artifacts) require care: a partially-completed run that gets cancelled may leave dangling state. None of the panakoes CI workflows currently have such side effects, but this is a constraint to remember when adding new ones.
- The negative case for cancellation (release.yml, scorecard.yml) requires a per-workflow judgement call that future contributors must make consciously, not by default. The doc-as-code approach is to leave the decision rationale in the workflow file's own comment.

## References

- `docs/operations/ci.md`, "Concurrency: cancel-in-progress" section.
- 2026-05-08 incident: 130+ queued jobs, 30+ minute queue depth, multiple PRs unmergeable until manually drained.
- GitHub documentation: [Using concurrency](https://docs.github.com/en/actions/using-jobs/using-concurrency).
- `feedback_panakoes_lessons.md` memory entry, "GitHub Actions runner queue exhaustion" section.
