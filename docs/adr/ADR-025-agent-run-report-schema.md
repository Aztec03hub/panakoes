# ADR-025: Agent Run Report Schema

## Status

Accepted.

## Context

Sub-agents (per ADR-024) are non-deterministic. They are large language models running focused implementation tasks in fresh context windows. Two runs with the same brief can differ in which decisions they make, which scope they expand into, and which edge cases they handle. Without a structured artifact per run, the orchestrator's only signal is whatever free-form summary the agent chose to write.

That is insufficient for three reasons:

1. **Verification.** The orchestrator needs to confirm specific facts: did `status` end as success? Do the files-touched fields match the actual diff? Did tests pass? Was coverage met? Were there em-dashes? A free-form summary can omit any of these without the orchestrator noticing.
2. **Rollback.** If a later run breaks something, the orchestrator needs an explicit, per-run rollback procedure. "Trust me, this is reversible" does not survive contact with a real bug six commits later.
3. **Audit.** Sub-agents are first-class observable subjects, not opaque black boxes. The same discipline as structured logging in production microservices applies: do not write opaque print statements, emit structured events that downstream tooling (here, the orchestrator's verification layer) can query.

The fix is a required structured artifact per agent run, with both machine-readable frontmatter and a human-readable body.

## Decision

Every Agent invocation that touches files MUST emit a run report at:

```
.agent-runs/<UTC-timestamp>-<short-slug>.md
```

The path is fixed. The timestamp is UTC, ISO 8601, with `-` separators safe for filenames (e.g. `2026-05-07T20-35-00Z`). The slug is short and descriptive.

The file has two sections.

**YAML frontmatter (machine-readable):**

```yaml
run_id: 2026-05-07T20-35-00Z-auth-service
agent_description: "Build Auth microservice (Better-Auth, TypeScript)"
started_at: "2026-05-07T20:35:00Z"
finished_at: "2026-05-07T21:02:18Z"
duration_seconds: 1638
status: success            # success | partial | failed
files_created: [...]
files_modified: [...]
files_deleted: []
commits_made: []
verification:
  build_clean: true
  tests_passing: true
  test_count: 23
  coverage_percent: 91.2
  coverage_threshold_met: true
  lint_clean: true
  em_dashes: 0
  type_check_clean: true
```

**Markdown body (human-readable):**

- **Summary.** One paragraph. What was accomplished.
- **What I Built.** Bullets or short prose. Reference specific files and acceptance criteria from the brief.
- **Decisions Beyond the Brief.** For each: the decision, why (constraints, alternatives considered), whether the orchestrator should review it before merge.
- **Issues Encountered.** For each: the issue and how it was addressed (or "deferred to follow-up if blocking").
- **Suggestions for Follow-up.** Improvements / refactors / scope expansions worth tracking but explicitly out of scope for this run.
- **Rollback Procedure.** Explicit steps. `git checkout HEAD~1 -- <file>` for uncommitted changes. `git revert <sha>` for committed changes. Any external side effects (AWS resources, external API calls) that cannot be reverted via git.

Reports are individually gitignored. Only `.agent-runs/README.md` is committed. Reports persist on Phil's local machine and are pruned periodically (older than 30 days). Anything that should be permanent record gets copied into the appropriate doc (CHANGELOG.md, PLANNING.md ADR, etc.) before pruning.

## Consequences

**Positive:**
- Agents have a forcing function for self-review. Filling out "Decisions Beyond the Brief" and "Rollback Procedure" requires the agent to reflect on what it did, which catches gaps it might otherwise wave away in a free-form summary.
- The orchestrator's verification checklist (per `.agent-runs/README.md`) anchors against concrete fields: `status`, `files_created`/`files_modified` versus the diff, `tests_passing`, `coverage_threshold_met`, `em_dashes: 0`. Every check has a single source of truth.
- Reports are local artifacts, which keeps the public repo clean of agent telemetry. Portfolio-relevant runs (notable architectural moments, large refactors) get curated into the repo over time.
- Rollback is testable. A future engineer can read the report and execute the rollback steps deterministically.

**Negative:**
- Every agent run incurs the cost of writing the report. Mitigated by templates in `.agent-runs/README.md` and by the report being a small fraction of total agent time on a non-trivial task.
- Reports drift if the schema is not enforced. Mitigated by the orchestrator rejecting any report missing required fields and re-delegating.
- Local-only persistence means a machine-loss event drops report history. Acceptable: significant history lives in `git log` and `CHANGELOG.md`, not here. Reports are operational telemetry, not the system of record.

## References

- `.agent-runs/README.md`, required format, example frontmatter, orchestrator verification checklist (full enumeration of the gates the orchestrator runs).
- `CLAUDE.md`, "Why structured run reports matter", the project-convention rationale for treating sub-agents as first-class observable subjects.
- `CLAUDE.md`, "Common Sub-Agent Briefs", templates that already include the run-report-required directive in every brief.
- ADR-024 (orchestrator-delegation pattern; the working mode that requires this artifact).
