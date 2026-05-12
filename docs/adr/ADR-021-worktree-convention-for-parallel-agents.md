# ADR-021: Worktree Convention for Parallel Sub-Agents

## Status

Accepted. Lived since 2026-05-07.

## Context

Panakoes is built with an orchestrator-delegation pattern (see ADR-024) in which the top-level Claude session spawns multiple sub-agents in parallel via the Agent tool. Each sub-agent does focused implementation work in its own branch and emits a structured run report.

When more than one sub-agent runs at the same time inside the same git working tree, they share a single `HEAD`, a single staging index, and a single set of untracked files. The failure modes are mechanical and immediate:

- `git add -A` in agent A picks up agent B's untracked scratch files.
- `git checkout -b ...` in one agent moves `HEAD` out from under another.
- `git stash` collisions corrupt mid-flight changes.
- Branch creation by name collides if both agents pick similar slugs.

We hit this on day one of the project. Two parallel agents shared the working tree, cross-polluted each other's commits, and we lost roughly thirty minutes untangling who-wrote-what before re-running the work cleanly. A second incident on night two (PR #22 silently included unrelated work because of a stale checkout off a non-`origin/main` base) confirmed that the failure mode also reaches the orchestrator's checkout state, not just sub-agent state.

The CLAUDE.md "Parallel sub-agents MUST use git worktrees" section codifies the rule. This ADR makes the underlying decision durable.

## Decision

Every sub-agent the orchestrator spawns to run **concurrently** with another sub-agent MUST be assigned a dedicated git worktree. The orchestrator creates the worktree before delegating:

```bash
cd ~/projects/panakoes
git worktree add ../panakoes-<task-slug> -b feat/<task-slug> origin/main
```

The `origin/main` base is mandatory. We do not allow implicit current-HEAD bases, because the orchestrator's HEAD may have drifted during a previous in-flight task and silently inheriting that state is the failure mode that produced the PR #22 incident.

The agent's brief specifies its working directory and confirms the branch is pre-checked-out:

```
WORKING DIRECTORY: ~/projects/panakoes-<task-slug>
The branch feat/<task-slug> is already checked out for you.
All git operations and file edits happen in this directory only.
```

When the agent's PR merges, the orchestrator removes the worktree and deletes the local branch:

```bash
cd ~/projects/panakoes
git worktree remove ../panakoes-<task-slug>
git branch -D feat/<task-slug>
```

Single-agent runs (no concurrency) MAY use the main checkout directly. The discipline applies only when more than one agent is in flight at the same time.

## Consequences

**Positive:**
- Sub-agents are isolated from each other. Each operates on its own `HEAD`, its own staging index, its own untracked-file set. The class of disaster that cost us thirty minutes on day one is structurally prevented.
- Branch base is unambiguous. Every concurrent feature branch starts from `origin/main`, so reviewers and CI both see a clean diff. No stale-base inclusion.
- The orchestrator is the only entity that mutates the canonical `panakoes/` checkout. Sub-agents cannot drag the orchestrator's HEAD around.

**Negative:**
- Per-agent setup and teardown overhead (`git worktree add`, `git worktree remove`, branch cleanup). Mitigated by orchestrator templates.
- Sibling directories proliferate during heavy parallel work (`panakoes-foo`, `panakoes-bar`, ...). Mitigated by removing on PR-merge.
- Disk usage is higher than a single shared tree. Acceptable on a developer laptop; worktrees share the `.git` object store, so the cost is mostly working-tree files, not history.

## References

- `CLAUDE.md`, "Parallel sub-agents MUST use git worktrees" section.
- Day-one incident: two parallel agents collided on a shared working tree and we lost ~30 minutes recovering the correct commit graph.
- Night-two PR #22-via-PR-#24 silent inclusion incident, which sharpened the `origin/main` base requirement.
- Phil's `feedback_panakoes_lessons.md` memory entry, which calls out worktrees as a hard-earned lesson alongside other night-one and night-two findings.
