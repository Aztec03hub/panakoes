# ADR-024: Orchestrator-Delegation as Default Working Mode

## Status

Accepted.

## Context

Phil works with Claude as a single user on a large engineering project. The Panakoes scope (six-plus microservices, infra, frontend, e2e tests, custom AMI, payments, observability) is engineering-heavy. Many of the work units are independent or only loosely coupled, which makes the work parallel-friendly.

Claude Code provides the Agent tool, which spawns a sub-agent in a fresh context window with its own instructions and conventions. The sub-agent runs to completion, returns a summary, and leaves behind whatever artifacts (files, commits, run report) it produced.

There are two reasonable ways to use this:

1. **Direct mode.** The top-level Claude does all the work itself in one long context. Simple, sequential, but burns the orchestrator's context window quickly and serializes work that could run in parallel.
2. **Orchestrator-delegation mode.** The top-level Claude decomposes the work, delegates focused sub-tasks to parallel sub-agents (each in its own worktree per ADR-021), verifies each agent's output, and integrates only verified work.

Direct mode is fine for small tasks. For a multi-week engineering project where Phil is solo and shipping evidence-of-depth for senior interviews, the parallelism and the verification gate of orchestrator-delegation is materially more valuable.

## Decision

Default working mode for Panakoes is **orchestrator-delegation**.

The orchestrator's responsibilities:

1. **Decompose.** Break the work into focused, independently shippable briefs. Each brief has acceptance criteria, scope (files in scope, files not in scope), and reiterates the non-negotiables (Conventional Commits, CHANGELOG update, no secrets, branch-and-PR, TDD).
2. **Spawn.** Create a worktree per ADR-021 for each concurrent agent. Pass the brief plus the working-directory directive.
3. **Verify.** When the agent returns, read the run report (ADR-025), confirm `status: success`, confirm `files_created`/`files_modified` match the actual diff, run `gitleaks` against the diff, run lint and type-check, review "Decisions Beyond the Brief" for items that warrant Phil's attention, confirm the rollback procedure is concrete.
4. **Integrate.** Only verified work merges. Reject and re-delegate if any gate fails. Don't rationalize; re-do.

**Direct mode** is the explicit exception. Use it for:

- Small tasks (single-line config edits).
- Decision conversations with Phil.
- File reads for orientation.
- Inherently sequential tasks with no parallelism gain.

The default is delegation. Direct mode requires a reason.

## Consequences

**Positive:**
- Phil sees only verified diffs. The orchestrator's verification layer catches the "agent claimed X but did Y" failure mode (which has happened: an agent claimed "no em-dashes" while leaving two in its output; verification caught it).
- Parallelism: independent work units run concurrently, compressing wall-clock time.
- The orchestrator's context window stays clear of implementation noise. It carries plans, decisions, and verification results, not the entire diff for every file.
- Sub-agents are first-class observable subjects, not opaque black boxes. The run report (ADR-025) makes their work auditable.

**Negative:**
- Verification overhead. The orchestrator must read every run report, diff every change, run every gate. This is real work, but it is the work that turns "trust the diff" into "verify against the contract."
- Agent briefs must be self-contained. Each brief reiterates CLAUDE.md prerequisites, acceptance criteria, scope, and discipline rules. Mitigated by templates in CLAUDE.md "Common Sub-Agent Briefs" section.
- Delegation has a fixed setup cost (worktree, brief, spawn). For trivial tasks the setup outweighs the parallelism gain, which is why direct mode exists.

## References

- `CLAUDE.md`, "Working Modes" section, full orchestrator-delegation procedure plus direct-mode exception criteria.
- `CLAUDE.md`, "Common Sub-Agent Briefs", templates for service implementation, test writing, and Terraform changes.
- ADR-021 (worktree convention; the structural prerequisite for safe parallel delegation).
- ADR-025 (agent run report schema; the artifact the verification layer reads).
- `PLANNING.md` ADR-021 (legacy register entry that this ADR supersedes for the working-modes decision; renumbered here for the formal ADR bundle).
