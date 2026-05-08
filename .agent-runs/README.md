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

## When orchestrator MUST verify the report

After every sub-agent completion, before integrating the work, the orchestrator checks:
1. Report exists at the expected path
2. `status: success` (otherwise re-delegate or escalate to Phil)
3. `files_created` and `files_modified` match the diff
4. `verification.tests_passing` and `verification.coverage_threshold_met` are both true (where applicable)
5. `verification.em_dashes: 0`
6. Decisions Beyond the Brief reviewed; flag anything that warrants Phil's attention
7. Rollback Procedure is concrete and testable

If any check fails, the work is rejected and the agent is re-delegated.
