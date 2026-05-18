# Agent Run Reports

This directory holds structured run reports from every sub-agent invocation that touches the repo. Reports are gitignored individually; this README is the only file in the directory that ships in version control.

## Why this exists

Sub-agents do focused implementation work delegated by the top-level orchestrator (Claude Code). Without telemetry, the orchestrator's verification of agent work depends on whatever the agent chose to summarize. Without persistence, prior runs become forgotten history.

Run reports turn each agent invocation into a first-class observable subject:
- **Verification:** orchestrator confirms files-touched and verification-results match the brief
- **Rollback:** explicit rollback procedure per run lets us undo a specific agent's work without guessing
- **Debugging:** if a subsequent run breaks something, prior reports show what changed and when
- **Audit:** transparent record of which agent did which thing, for retrospective review

This is the same discipline as structured logging in production microservices: don't write opaque print statements; emit structured events that downstream tooling can query.

## Required format

Every agent run that modifies files MUST produce a report at:

```
.agent-runs/<UTC-timestamp>-<short-slug>.md
```

Example: `.agent-runs/2026-05-07T20-35-00Z-auth-service.md`

The file has YAML frontmatter (machine-readable) followed by markdown body (human-readable):

```markdown
---
run_id: 2026-05-07T20-35-00Z-auth-service
agent_description: "Build Auth microservice (Better-Auth, TypeScript)"
started_at: "2026-05-07T20:35:00Z"
finished_at: "2026-05-07T21:02:18Z"
duration_seconds: 1638
status: success            # success | partial | failed
files_created:
  - services/auth/src/index.ts
  - services/auth/tests/integration/auth.test.ts
files_modified:
  - CHANGELOG.md
  - services/_template/README.md
files_deleted: []
commits_made: []           # if the agent committed; otherwise empty
verification:
  build_clean: true
  tests_passing: true
  test_count: 23
  coverage_percent: 91.2
  coverage_threshold_met: true
  lint_clean: true
  em_dashes: 0
  type_check_clean: true
---

# Agent Run Report: Build Auth microservice

## Summary
One-paragraph narrative of what was accomplished.

## What I Built
Bullets or short prose describing the actual work. Reference specific files and acceptance criteria from the brief.

## Decisions Beyond the Brief
For each:
- The decision
- Why I made it (constraints, alternatives considered)
- Whether the orchestrator should review it before merge

## Issues Encountered
For each:
- The issue
- How I addressed it (or "deferred to follow-up if blocking")

## Suggestions for Follow-up
- Improvements / refactors / scope expansions worth tracking but explicitly out of scope for this run.

## Rollback Procedure
Explicit steps to undo this run:
- `git checkout HEAD~1 -- <file>` for uncommitted changes
- `git revert <sha>` for committed changes
- Any external side effects (AWS resources, external API calls) that cannot be reverted via git
```

## Directory hygiene

- Reports are gitignored individually; only this README is committed.
- Reports persist across sessions on Phil's local machine.
- Cleanup: prune old reports (older than 30 days) periodically. Significant history lives in git log + CHANGELOG.md, not here.
- If a report contains anything that should be permanent record, copy that information into the appropriate doc (CHANGELOG.md, PLANNING.md ADR, etc.) before pruning.

## Progress Log (mandatory)

Every agent dispatch produces TWO artifacts under `.agent-runs/`:

1. The **final run report** (markdown, schema above), written at the end of the run.
2. A **streaming progress log** (`.progress.log` extension), appended to during execution.

The progress log gives the orchestrator mid-run observability without invading the agent's JSONL transcript (which the harness explicitly forbids reading). It is dogfooded as the canonical inter-run telemetry channel: anyone can `tail -f .agent-runs/<run-id>.progress.log` while the agent runs and see where it is.

### File naming

The progress log lives at:

```
.agent-runs/<run-id>.progress.log
```

The `<run-id>` is identical to the final report's run-id (same UTC timestamp + slug); only the extension differs. On completion, both files are present in the directory and form a matched pair.

Both `*.md` and `*.progress.log` under `.agent-runs/` are gitignored (this README is the only file in the directory that ships in version control).

### Line format

One line per checkpoint:

```
[ISO-8601-UTC-timestamp] [STEP] human-readable message
```

Example log from a Terraform-only agent run:

```
[2026-05-18T19:35:00Z] [START] dispatched, reading prereqs
[2026-05-18T19:35:15Z] [PREREQ-DONE] read CLAUDE.md, ARCH-MIGRATION.md section 2.2, .agent-runs/README.md
[2026-05-18T19:36:30Z] [FILES-WRITTEN] infra/dev/kms/{providers,variables,data,main,outputs}.tf + README
[2026-05-18T19:37:00Z] [INIT-DONE] terraform init: providers cached, S3 backend ok
[2026-05-18T19:39:00Z] [PLAN-DONE] 4 add / 0 change / 0 destroy
[2026-05-18T19:39:22Z] [REPORT-WRITTEN] /home/aztec/projects/panakoes-w2-t1-kms/.agent-runs/2026-05-18T19-39-22Z-w2-t1-kms-new-keys.md
[2026-05-18T19:39:22Z] [DONE] status=success
```

### Canonical STEP names

Use these on the happy path so logs are mechanically comparable across runs:

- `START` -- dispatch received, agent is alive and beginning
- `PREREQ-DONE` -- prerequisite reading complete (list which files in the message)
- `FILES-WRITTEN` -- all source/config files created or edited
- `INIT-DONE` -- `terraform init` complete (Terraform tasks only)
- `PLAN-DONE` -- `terraform plan` complete; include add/change/destroy counts in the message
- `VALIDATE-DONE` -- `terraform validate` or equivalent schema check passed
- `TESTS-DONE` -- unit + integration tests complete; include pass/fail count
- `LINT-DONE` -- lint and formatter clean on changed files
- `CI-FAST-DONE` -- `make ci-fast` clean
- `COMMIT-WRITTEN` -- commit created (include short SHA in the message)
- `REPORT-WRITTEN` -- run report written at the expected path (include path in the message)
- `DONE` -- terminal success; message includes `status=success`

For non-happy-path branches:

- `BLOCKED <reason>` -- the agent has hit a stop condition and is surfacing to the orchestrator without pushing through
- `ESCALATING <reason>` -- scope or risk has expanded; the agent is presenting the three escalation options per the `CLAUDE.md` "Sub-agent escalation pattern"
- `RETRY <step>` -- a step failed; the agent is taking its one allowed fix-attempt before deciding to BLOCK

### Cadence expectation

If a step takes more than 5 minutes without a new checkpoint line, the orchestrator should investigate (read the agent's working state, consider whether to terminate and re-dispatch). The 5-minute threshold matches the rough cost of a wasted Terraform plan or a CI cycle; longer silence indicates the agent is stuck on a tool wait, an infinite loop, or has lost its task focus.

## When orchestrator MUST verify the report

After every sub-agent completion, before integrating the work, the orchestrator checks:
1. Report exists at the expected path
2. `status: success` (otherwise re-delegate or escalate to Phil)
3. `files_created` and `files_modified` match the diff
4. `verification.tests_passing` and `verification.coverage_threshold_met` are both true (where applicable)
5. `verification.em_dashes: 0`
6. Decisions Beyond the Brief reviewed; flag anything that warrants Phil's attention
7. Rollback Procedure is concrete and testable
8. Progress log (`.agent-runs/<run-id>.progress.log`) shows a clean sequence ending in `[DONE] status=success`. If the last line is `BLOCKED` or `ESCALATING`, read the run report's escalation block and surface to Phil before integrating. If the log has gaps over 5 minutes that the report does not explain, ask the agent (or the next dispatch) to clarify.

If any check fails, the work is rejected and the agent is re-delegated.
