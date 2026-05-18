# ADR-045: File-defined long-running agents under `.claude/agents/`

## Status

Accepted (2026-05-18). First instance: `.claude/agents/dependency-updater.md`
landed in PR #364.

## Context

Every sub-agent dispatch in Panakoes follows the orchestrator-delegation
pattern (ADR-024): the orchestrator writes a brief, the agent runs in a
worktree (ADR-021), the agent emits a run report (ADR-025), the orchestrator
verifies and integrates. Until now every brief was inline: the orchestrator
typed (or pasted from a template) the full system prompt for every dispatch.

That works for one-shot tasks. It breaks down for recurring jobs. The
dependency-updater pattern, for example, has roughly two pages of stable
instructions: which ecosystems to audit, what `make ci-fast` contract to
respect, how to categorize bumps as patch / minor / major, which Dependabot PR
flow to use, what stop conditions apply. Re-typing or re-copying those two
pages on every invocation wastes roughly fifteen minutes of orchestrator
context-window per dispatch and invites copy-paste drift between sessions.

Two adjacent artifact classes exist now, and the boundaries between them were
becoming unclear:

- `docs/templates/agent-brief.md` is the canonical skeleton for one-shot inline
  briefs (PR #368). It is a template, not an agent; the orchestrator fills it
  in per dispatch.
- `.agent-runs/<run-id>.md` is the ephemeral post-run audit trail (ADR-025),
  gitignored, written by the agent at the end of its run.

A third class is the recurring agent definition: a system prompt plus a
workflow plus a stop-condition checklist that lives in the repo, is reviewed
in PRs, and is dispatched by name across sessions.

## Decision

Recurring sub-agents are defined as Markdown files under `.claude/agents/`,
versioned in the repo, reviewable in PRs, and dispatched by name across
sessions. One-shot agents continue to use inline briefs based on
`docs/templates/agent-brief.md`. Both produce run reports under
`.agent-runs/`.

File-defined agents carry YAML frontmatter with `name`, `description` (the
trigger-phrase contract the orchestrator matches against), `model`, `color`,
and `tools` (the explicit tool allowlist for the agent). The body contains:

- "When to invoke" with worked scenarios (backlog clearance, specific
  migration, scheduled run, security-driven update, etc.).
- "Core Responsibilities" describing the agent's job.
- "Discovery Process" and per-task workflows.
- "Stop conditions" and escalation patterns matching `CLAUDE.md`.
- "Output contract" (what the agent's final return value must contain).

`CLAUDE.md` gains a "Project Agents" section that enumerates the files under
`.claude/agents/` and the trigger phrases for each. The orchestrator dispatches
by name (e.g. "dispatch the dependency-updater agent on the backlog") instead
of by inline prompt.

## Consequences

**Positive.**

- Recurring agent definitions are version-controlled and reviewable like code.
  A change to the dependency-updater workflow goes through a PR with diff
  review; drift between sessions is structurally prevented.
- The orchestrator's dispatch shrinks from a multi-page inline brief to a
  one-line reference, freeing context window for the actual decomposition and
  verification work.
- Future sessions auto-discover any agent added between sessions; no
  per-session bootstrap to teach the new agent.
- A clear three-way separation emerges: `.claude/agents/<name>.md` (long-lived,
  PR-reviewed, dispatchable by name), `docs/templates/agent-brief.md`
  (one-shot skeleton, copy-paste-and-fill), `.agent-runs/<id>.md`
  (ephemeral audit trail, gitignored). Each artifact class has a single
  purpose and a clear lifetime.

**Negative.**

- Two patterns now coexist (file-defined and inline). New contributors must
  understand when each applies. Mitigation: the file-defined pattern is for
  recurring work only; the default is still inline, and `CLAUDE.md`'s
  "Working Modes" section documents the rule.
- A file-defined agent's tool allowlist is now part of the repo; tightening or
  expanding it ships as a PR like any other change. The friction is real but
  intentional, since the tool surface area is a security-relevant decision.
- Discovery cost on first session: a contributor pointed at the repo must
  read both the inline-brief template and the file-defined agents to know
  what is available. Mitigated by the `CLAUDE.md` "Project Agents" section
  acting as the index.

## Alternatives considered

**Keep every dispatch inline.** Rejected: re-typing or re-copying multi-page
briefs on every recurring dispatch wastes orchestrator context-window
(~fifteen minutes per dispatch) and invites silent drift between sessions
when one orchestrator's copy diverges from another's.

**Embed all recurring agent definitions in `CLAUDE.md`.** Rejected: bloats
the file Claude loads on every session. Agent definitions should be loaded
on demand (when the agent is actually dispatched), not eagerly on every
prompt. `CLAUDE.md` is the project's working-conventions index, not its
prompt catalog.

**Use a private `~/.claude/agents/` directory under the user's home.**
Rejected: agent definitions are project-scoped (they reference Panakoes
service names, repo paths, ADR numbers), benefit from PR review, and need to
be shared across collaborators or future LaFayette Labs team members. A home-
directory location loses all three properties.

**Use a separate `.claude/prompts/` directory rather than `.claude/agents/`.**
Rejected: the existing Claude Code agent-discovery convention is the
`.claude/agents/` path with frontmatter. Building on the convention is cheaper
than inventing a parallel one.

## References

- PR #364 (introduced `.claude/agents/dependency-updater.md`, the first
  instance of the pattern).
- PR #368 (introduced `docs/templates/agent-brief.md`, the canonical inline
  brief skeleton).
- ADR-024 (orchestrator-delegation pattern).
- ADR-025 (agent run report schema, the ephemeral artifact class).
- `.claude/agents/dependency-updater.md` (the reference implementation).
- `CLAUDE.md` "Working Modes" and "Project Agents" sections.
