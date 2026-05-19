# Canonical Agent Brief Template

This is the canonical starting point for every sub-agent dispatch in the Panakoes orchestrator-delegation workflow. Copy the body below (the section starting at "Agent Brief:") into the `prompt` field of an Agent tool call, fill in every `<<placeholder>>`, decide the push/PR toggles, and dispatch.

## How to use this template

1. Pick a short kebab-case slug for the task (e.g. `kms-new-keys`, `auth-revocation-flow`, `ses-bootstrap`).
2. Create a worktree off `origin/main`: `cd ~/projects/panakoes && git worktree add ../panakoes-<slug> -b feat/<slug> origin/main` (or `chore/`, `fix/`, etc.).
3. Copy the template body below into your Agent tool invocation as the `prompt` argument.
4. Fill in every `<<placeholder>>`. Do not leave any.
5. Decide each toggle in "Push behavior" and "PR behavior" before dispatch (one checked, one not).
6. Dispatch. Verify the resulting run report and progress log per `.agent-runs/README.md` when the agent returns.

The inline templates in `CLAUDE.md`'s "Common Sub-Agent Briefs" section (service implementation, test-writing, Terraform) are pre-filled examples of this skeleton for the most common patterns; keep them in sync if the skeleton evolves. The Wave-N dispatch briefs in `ARCH-MIGRATION.md` section 7 are worked examples for in-flight architecture migrations and should mirror this shape.

For long-running, file-defined agents (the ones that live in `.claude/agents/` as their own contract files), see `.claude/agents/dependency-updater.md` as the canonical example; those carry richer "When to invoke" + "Core Responsibilities" sections and are dispatched by name rather than by inline prompt.

---

# Agent Brief: <<short task name>>

## Working context (orchestrator fills before dispatch)

- **WORKING DIRECTORY:** /home/aztec/projects/panakoes-<<slug>>
- **BASE COMMIT:** <<full sha + branch name on origin, e.g. `8e5e67c (origin/main as of dispatch)`>>
- **BRANCH:** <<branch name, e.g. `feat/<slug>` or `chore/<slug>`>>
- **DISPATCH TYPE:** background | foreground
- **ORCHESTRATOR:** <<session id, or "Phil-as-orchestrator" if hand-dispatched>>

## Prerequisite reading

(Always at minimum: `CLAUDE.md`, `.agent-runs/README.md`, plus task-specific context. List the EXACT files and section pins; do not say "read the docs".)

1. <<absolute or repo-relative file path, with section pin where useful>>
2. <<file path>>
3. <<file path>>

## Task

<<concise task statement, 1-3 sentences. State the outcome, not the steps. The agent decides the steps.>>

## Expected files modified

(Mandatory: declare your file/glob set BEFORE you touch anything, so the orchestrator can run `scripts/check-agent-overlap.py` against sibling agents and detect collisions before dispatch.)

- <<path or glob>>
- <<path or glob>>
- `.changelog/<UTC-timestamp>-<slug>.md` (unless this PR is in the changelog-check exempt list)

## Acceptance criteria

(Testable bullets. The agent must be able to point at each one and prove it. Vague bullets ("works well", "looks good") are not acceptance criteria.)

- [ ] <<testable criterion>>
- [ ] <<testable criterion>>
- [ ] <<testable criterion>>

## Local-first verification (mandatory)

Before declaring complete OR writing the run report, run these commands and capture their full output. The output goes into the run report's "Local-First Verification" section verbatim, no truncation.

For Terraform changes:

```bash
cd infra/dev/<<module>>
terraform fmt -recursive
terraform validate
terraform plan -out=tfplan.bin
```

For Python services:

```bash
cd services/<<svc>>
uv run pytest -m "not integration"
uv run pytest
```

For TypeScript services:

```bash
cd services/<<svc>>
pnpm run typecheck
pnpm run test
```

Plus, always, from the repo root:

```bash
make ci-fast    # gitleaks + em-dash + actionlint + tf fmt + ruff (changed files only), under 90s
```

If any verification fails: one fix-attempt is allowed; if it still fails after that, STOP and surface to the orchestrator with the failure context. Do not push a broken PR.

## Discipline (non-negotiable, repeated)

- No em-dashes anywhere (commits, comments, docs, .tf, .py, .ts, fragments). Use commas, colons, parens, semicolons, or `-` hyphens.
- Conventional Commits: `<type>(<scope>): <subject>` lowercase. Types: feat, fix, chore, docs, refactor, test, style, ci, perf, build, security.
- Drop one `.changelog/<UTC-timestamp>-<slug>.md` fragment (unless this PR is `docs/`, `.github/`, `scripts/`, `.githooks/`, `Makefile`, `.gitignore`, `.pre-commit-config.yaml`, `.gitleaks.toml`, or root-`CLAUDE.md`/`PLANNING.md`/`SCOPE.md`/`WORKFLOW.md`/`FOLLOWUPS.md`/`CONTRIBUTING.md`/`SECURITY.md`/`LICENSE`/`README.md` scoped per the `.github/workflows/changelog-check.yml` exempt list).
- No `NO_VERIFY=1 git push`. The pre-push hook is the gate; if it is in the way, fix the hook in a separate PR.
- No secrets in code; lock files and tfstate stay outside the repo.
- No touching `panakoes-hardware/` (off-limits per `CLAUDE.md`).
- Single squash commit per PR (multiple commits on the branch are fine before merge; the merge squashes).

## Stop conditions

(When to halt and surface to the orchestrator instead of pushing through:)

- `terraform plan` shows replacements or destroys that were not in the brief (orchestrator must add the `replace-allowed` label before CI runs).
- Local tests fail and one fix-attempt loop does not recover.
- You discover a cross-cutting issue beyond this PR's scope (per `CLAUDE.md` "Cross-cutting findings reflex"). Note it for the orchestrator to spawn a dedicated follow-up agent.
- Scope realistically exceeds about 4 hours of work (per `CLAUDE.md` "Sub-agent escalation pattern"). Surface the three options: defer to backlog, decompose into N smaller PRs (with proposed split), or push through past the time box.
- You would need to make a product or architecture decision Phil has not pre-approved.

## Push behavior

(Pick one per dispatch.)

- [ ] DO push when done (default for routine work).
- [ ] DO NOT push; orchestrator reviews the run report and pushes after verification (default for infra changes with replacements or destroys, security-sensitive changes, or anything where Phil wants a human gate).

## PR behavior

(Pick one per dispatch.)

- [ ] DO open the PR with `gh pr create --fill` (or with an explicit title and body if you prefer).
- [ ] DO NOT open the PR; orchestrator opens it after reviewing locally.

## Required run report

At `.agent-runs/<UTC-timestamp>-<slug>.md` per the schema in `.agent-runs/README.md`. Minimum sections (more allowed):

- YAML frontmatter: `run_id`, `agent_description`, `started_at`, `finished_at`, `duration_seconds`, `status` (success | partial | failed), `files_created`, `files_modified`, `files_deleted`, `commits_made`, and a `verification:` block with `build_clean`, `tests_passing`, `test_count`, `coverage_percent`, `coverage_threshold_met`, `lint_clean`, `em_dashes`, `type_check_clean` (omit fields that do not apply to this task).
- Body sections: Summary, What I Built, Decisions Beyond the Brief, Local-First Verification (with FULL command output, no truncation), Issues Encountered, Suggestions for Follow-up, Rollback Procedure, Status.

## Progress log (mandatory)

See `.agent-runs/README.md` "Progress Log" section for the full spec.

Append a timestamped checkpoint line to `.agent-runs/<run-id>.progress.log` (same `<run-id>` as the final report, just the `.progress.log` extension) after each major step. Format:

```
[ISO-8601-UTC-timestamp] [STEP] human-readable message
```

At minimum, log: `START`, `PREREQ-DONE`, `FILES-WRITTEN`, `INIT-DONE` (if Terraform), `PLAN-DONE` (if Terraform), `VALIDATE-DONE`, `TESTS-DONE`, `LINT-DONE`, `CI-FAST-DONE`, `COMMIT-WRITTEN`, `REPORT-WRITTEN`, `DONE`. Use `BLOCKED <reason>`, `ESCALATING <reason>`, or `RETRY <step>` for non-happy-path branches.

**The DONE line is required and must include `status=success` OR `status=failure`** (e.g. `[2026-05-19T00:30:00Z] [DONE] status=success` or `[2026-05-19T00:30:00Z] [DONE] status=failure: terraform plan errored on unknown variable`). `scripts/verify-agent-run.sh` enforces this. An absent DONE line is treated as "agent crashed" and triggers different recovery; a DONE with neither status token is treated as malformed. Failure is a valid clean terminal for runs that hit BLOCKED / ESCALATING earlier; do not omit the DONE line just because the run failed.

If a step takes more than 5 minutes without a new checkpoint line, the orchestrator should investigate.

## Final return value

A summary under 200 words pointing the orchestrator at:

- The run report path (absolute).
- The progress log path (absolute).
- Headline numbers (terraform plan add/change/destroy, tests passed/failed, coverage delta).
- Anything that needs Phil's attention before push or merge: a `replace-allowed` label needed, a security-relevant finding, an out-of-scope item flagged, a decision-beyond-the-brief that warrants review.

Keep the return value tight; the detail lives in the run report.
