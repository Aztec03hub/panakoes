# ADR-046: Local-first verification as orchestrator discipline

## Status

Accepted (2026-05-18). First explicit instance: PR #365 (Wave 2 KMS).
Second instance: PR #369 (Tier 1).

## Context

PR #363 (disable Container Insights, see ADR-044) shipped without local-first
verification because the orchestrator was on a fresh dev machine without the
local toolchain installed. The orchestrator dispatched, the agent ran the
change through Terraform CI server-side, the diff merged. Phil caught the
discipline failure mid-session: "we had discussed doing dev locally FIRST, and
verifying EVERYTHING local until reasonable testing points were done, and only
THEN pushing and merging".

The pattern Phil was preserving has roots in earlier incidents. PR #232 (em-
dash slipped through under a `NO_VERIFY=1` push) and PR #239 (Terraform plan
showed an unexpected destroy that local-plan would have caught) were both
"small" changes that broke server-side and consumed cycles because the local
gate was skipped. The lesson from those incidents was already encoded in the
`make ci-fast` pre-push hook and in `CLAUDE.md`'s discipline rules, but the
discipline was not consistently propagated into agent briefs.

The subsequent dispatches in the same session (PR #365 Wave 2 KMS, PR #367
devalue, PR #369 Tier 1) reset the pattern: every brief explicitly required
`terraform fmt + validate + plan`, `pytest` (where Python services were
touched), `pnpm test` (where TypeScript was touched), and `make ci-fast`, with
FULL output captured in the run report. The orchestrator's downstream
verification (read report, `git diff`, em-dash grep, gitleaks, CI status) became
the second-line check on top of the agent's first-line local gate, not a
substitute for it.

`docs/templates/agent-brief.md` (PR #368) codified the brief skeleton with a
mandatory "Local-First Verification" section. This ADR records the underlying
decision so the discipline survives template churn.

## Decision

Every sub-agent brief MUST include a "Local-First Verification" section that:

1. Names the exact commands to run for the change in scope:
   - Terraform: `terraform fmt -recursive`, `terraform validate`, `terraform
     plan -out=tfplan.bin` in the affected module.
   - Python: `uv run pytest -m "not integration"`, then `uv run pytest` if
     integration coverage is in scope.
   - TypeScript: `pnpm run typecheck`, `pnpm run test`.
   - Always, from the repo root: `make ci-fast` (gitleaks + em-dash +
     actionlint + tf fmt + ruff on changed files, under 90 seconds).
2. Requires the agent to capture the FULL command output verbatim in the run
   report's "Local-First Verification" section. No truncation, no "tests
   passed" summary in place of the actual output.
3. Defines a stop condition: if any check fails, the agent investigates once,
   attempts one fix, and if the check still fails STOPS and surfaces to the
   orchestrator with the failure context. No shipping broken work. No
   `NO_VERIFY=1`.

The orchestrator's trust-but-verify cycle (read run report, `git diff`, em-dash
grep, gitleaks, CI status) is the second-line check on top of the local-first
verification, not a substitute for it. The server-side CI gate is the THIRD
line, not the first.

Exception: tasks with no buildable surface (pure documentation, ADR writing,
runbook authoring) only need `make ci-fast` plus the em-dash grep. The
verification section in such briefs lists just those commands.

Bootstrap exception: when the orchestrator is on a fresh machine without the
local toolchain, the orchestrator MUST install the toolchain before
dispatching, not skip the verification. The fresh-machine state is the
failure-mode that produced PR #363; it is not a license to bypass the
discipline.

## Consequences

**Positive.**

- Every brief includes the verification section; every run report includes
  the verification output. The artifact trail makes the discipline visible
  and auditable.
- The orchestrator's mental model shifts from "dispatch and trust DONE" to
  "dispatch and verify against report". Run reports become first-class audit
  artifacts (per ADR-025) rather than optional disclosures.
- Rare failure modes where the local environment differs from CI (e.g. a
  provider plugin pinned at a different version locally vs in CI) are
  contained because the orchestrator can compare local plan output against
  CI plan output before merge.
- CI minutes and reviewer attention stop absorbing the cost of broken
  pushes. Server-side CI catches the rare environmental drift, not the
  routine em-dash or unformatted Terraform.

**Negative.**

- Dispatch latency grows by the duration of the local verification, ranging
  from sub-90-seconds for `make ci-fast` only, to several minutes for a full
  `terraform plan` plus integration tests. Net win: failing fast locally
  beats failing slow on CI plus a rebase round.
- Briefs are longer. The verification section adds 10-20 lines per brief.
  The cost is paid once at brief authoring and amortized across every
  dispatch using the template.
- Fresh-machine sessions pay a one-time toolchain install cost (uv, pnpm,
  terraform, gitleaks, pre-commit) before the first dispatch. Documented in
  the project bootstrap runbook.

## Alternatives considered

**Trust the server-side CI gate.** Rejected: CI is the LAST gate, not the
first. Broken pushes consume CI minutes plus reviewer attention plus
rebase-cascade time across sibling PRs. The cost is real and recurrent.
Every PR #232 and PR #239 lesson learned was a "small" change that broke
server-side because the local gate was skipped.

**Skip local verification when the change is small.** Rejected: every
incident on the night-two session was a "small" change. Em-dash slipping
through under time pressure, Terraform pin resolving to a broken SHA, auth-
service image baked before the migration landed, all "small" changes. There
is no reliable predictor of "small" up front.

**Accept that fresh-machine bootstrapping is special.** Rejected: the
discipline applies to every dispatch, fresh-machine or not. The fresh-
machine case just makes the failure mode more visible. The fix is to install
the toolchain before dispatching, not to make an exception for it.

**Move the verification to a server-side pre-commit job.** Rejected: server-
side execution defeats the "fail fast locally" goal. The push has already
happened; the rebase cascade has already started; the CI minutes are
already being spent. The pre-push hook is the right layer because it
catches the failure before it leaves the developer's machine.

## References

- PR #363 (the discipline-failure case that triggered the reset).
- PR #365 (Wave 2 KMS, first explicit local-first brief).
- PR #369 (Tier 1, second explicit local-first brief).
- PR #368 (`docs/templates/agent-brief.md`, the canonical brief skeleton
  with the "Local-First Verification" section).
- PR #232 (em-dash slipped through under `NO_VERIFY=1`; the pattern this ADR
  prevents).
- ADR-024 (orchestrator-delegation pattern; this ADR sharpens its
  verification contract).
- ADR-025 (agent run report schema; the artifact that carries the
  verification output).
- `Makefile` `ci-fast` target (the sub-90-second pre-push gate).
- `CLAUDE.md` "Discipline Rules" section.
