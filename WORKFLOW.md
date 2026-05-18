# WORKFLOW.md: How We Work on Panakoes

This file complements `CLAUDE.md`. `CLAUDE.md` describes WHAT the project is (decisions, structure, rules). `WORKFLOW.md` describes HOW Claude and Phil work together day to day: the rhythms, the rituals, the tool patterns, the failure modes to avoid, and the self-assessment loop that keeps the workflow improving instead of decaying.

`WORKFLOW.md` is a living file. Update it whenever a pattern stabilizes, a friction recurs, or a tool gotcha bites. Update it in the same PR (or a tight follow-up PR) that proved the pattern. Stale workflow docs are worse than missing ones because they teach the wrong reflex.

> A fresh Claude instance, starting cold, should be able to read `CLAUDE.md`, then `WORKFLOW.md`, then `MEMORY.md` (project memory), and within ten minutes be operating with the same effectiveness as the prior instance. That is the design goal of these three files together.

---

## 1. First five minutes of a session

Whether you are a returning Claude or a fresh instance, the bootstrap is identical:

1. Read `CLAUDE.md` once for project conventions and locked decisions.
2. Read this file (`WORKFLOW.md`) for working rhythms and patterns.
3. Read `/home/plafayette/.claude/projects/-mnt-c-Users-plafayette-Documents-Facebook/memory/MEMORY.md`. The harness loads the first ~200 lines automatically; scan past the auto-loaded section if you anticipate Panakoes work, paying attention to entries with names beginning `panakoes_`, `workflow_`, or `feedback_`.
4. Run `git status` and `git log --oneline -20` in `~/projects/panakoes` to see the current branch state.
5. Run `git worktree list` to see what parallel work is in flight (each entry is roughly 800 MB to 1.2 GB of disk; pile-up matters).
6. Check open PRs: `gh pr list --json number,title,headRefName,mergeStateStatus --limit 30`.

If Phil opened the session with a specific ask, do not delay it for a full status sweep. Read what you need to answer the ask, and let the rest of the orientation happen as it becomes load-bearing.

---

## 2. The work loop

For any non-trivial task, the loop is:

**Plan → Execute → Verify → Ship → Reflect.**

- **Plan.** State in one sentence what you are about to do, in user-facing text, before the first tool call. If the task is large or branching, decompose it explicitly (TaskCreate is fine, a short bulleted list in chat is also fine).
- **Execute.** Use the most direct tool for the job (Read/Edit/Write/Bash, not Bash for things a dedicated tool can do). If three or more parallel sub-tasks exist, dispatch sub-agents on worktrees. Otherwise do it inline.
- **Verify.** Read the diff yourself; do not trust a sub-agent's "DONE" message. Run `make ci-fast` (or the closest equivalent) BEFORE pushing. For UI changes, screenshot in the browser. For backend changes, exercise the endpoint or query the DB. For Terraform, run `terraform plan` and confirm the change set matches intent.
- **Ship.** The canonical PR flow is below (section 5). Auto-merge fires async; do not babysit it.
- **Reflect.** When the task involved real friction (more than one false start, a 3-strike pattern, a tool gotcha you had to discover), capture the fix here, in `CLAUDE.md`, or in a memory file. Do not let the lesson live only in chat context that will compact away.

**Default to delegation, not execution.** If a sub-agent type fits the task (Explore for read-only search, Plan for design, svelte-file-editor for any `.svelte` file edit), use it. Direct execution is the explicit exception for tasks too small, too coupled, or too sequential to delegate.

**"For exploratory questions, answer in 2-3 sentences with a recommendation and the main tradeoff."** This is the default. Do not propose an implementation plan until Phil agrees on the direction. The most expensive failure mode is shipping code Phil did not ask for; the second most expensive is asking for permission on something trivial.

---

## 3. Memory: read first, write deliberately

Memory is `~/.claude/projects/-mnt-c-Users-plafayette-Documents-Facebook/memory/`. The index file is `MEMORY.md` (always partially loaded into context). Topic files live alongside it as `<kebab-slug>.md` with YAML frontmatter.

**When to read:** at the start of any session, before relying on a piece of folk knowledge, and whenever Phil references prior conversation work.

**When to write:**
- A user preference, role detail, or context Phil told you.
- A correction Phil gave you, OR a non-obvious approach Phil validated. Save both.
- A project state fact that is not derivable from the code or git history (who is doing what, why, by when).
- A pointer to an external system (Linear board, Grafana dashboard, AWS console URL).

**When NOT to write:** code patterns, file paths, conventions, or anything `git log` / `grep` would surface.

**Hygiene:**
- Each new memory file gets its own `<slug>.md` with frontmatter (`name`, `description`, `metadata.type`).
- Add a one-line index entry to `MEMORY.md` under ~150 characters: `- [Title](file.md) -- one-line hook`.
- Link related memories with `[[other-name]]` in the body.
- Keep `MEMORY.md` lean. It is an index, not a memory.
- Before recommending action based on a memory that names a file/function/flag, **verify the named thing still exists**. Memories age; the code is the source of truth.

---

## 4. Sub-agents and worktrees

**Worktree-per-parallel-agent is mandatory.** See `CLAUDE.md` for the detailed rule and the failure case it prevents. The short version:

```bash
cd ~/projects/panakoes
git worktree add ../panakoes-<slug> -b feat/<slug> origin/main
```

Always base off `origin/main`, never off the orchestrator's HEAD. Always anchor the command at the repo root, never from a subdirectory. Always sibling location, never nested.

**Prune eagerly.** As soon as the sub-agent ships its PR, remove its worktree even before the PR merges:

```bash
git worktree remove --force ../panakoes-<slug>
```

A handful of stale worktrees is fine; ten of them is disk pressure that compounds. WSL2 root volume is around 100 GB and we have crossed 95% twice this month.

**Sub-agent briefs declare `EXPECTED FILES MODIFIED`** so the orchestrator can detect overlap and batch before dispatch. See `CLAUDE.md`'s "PR batching" section.

**The canonical brief skeleton lives at [`docs/templates/agent-brief.md`](docs/templates/agent-brief.md).** Copy its body into the Agent tool's `prompt` field, fill the placeholders, decide the push/PR toggles, and dispatch. The inline templates in `CLAUDE.md`'s "Common Sub-Agent Briefs" section and the wave-specific briefs in `ARCH-MIGRATION.md` section 7 are pre-filled examples of that skeleton.

**Sub-agents write structured run reports** at `.agent-runs/<UTC-timestamp>-<slug>.md` per `.agent-runs/README.md`, AND a streaming progress log at `.agent-runs/<run-id>.progress.log` so the orchestrator has mid-run observability. The orchestrator reads the report after the agent returns and verifies `files_modified` against `git status`, plus reads the progress log to confirm a clean sequence ending in `[DONE] status=success`. Run reports and progress logs are local-only (gitignored); anything that deserves permanent record gets copied into `CHANGELOG.md`, `PLANNING.md`, a runbook, or memory before pruning.

**ALWAYS assess sub-agent worklogs after termination** (default-on). Outcome verification via authoritative source (not "trust the DONE message"), channel-post cadence and gaps, discipline against the agent file, hidden quality issues, proposed agent-file edits, capability gaps. See `feedback_post_subagent_assessment.md` in memory for the assessment template.

**The svelte-worker is a standing background agent** dispatched via the `claude-comms` MCP server on the `svelte-work` conversation. It owns ALL Svelte/SvelteKit work. See `workflow_svelte_worker_dispatch.md` in memory. All Svelte work is runes-mode only with no size cap on legacy migration (see `feedback_all_svelte_work_is_runes_mode.md`).

---

## 5. Shipping a PR: the 10-second flow

```bash
cd ~/projects/panakoes-<slug>     # or whichever worktree
git push -u origin HEAD            # pre-push hook runs make ci-fast (<=90s)
gh pr create --fill
gh pr merge --squash --auto --delete-branch
```

Then prune the worktree:

```bash
cd ~/projects/panakoes
git worktree remove --force ../panakoes-<slug>
```

`ci-fast` runs gitleaks + em-dash detector + actionlint + tf fmt + path-scoped ruff, all on changed files, in under 90 seconds. The pre-push hook enforces it; do not `NO_VERIFY=1` push around it. Slow checks (pytest, vitest, mypy, full pre-commit) run server-side as `ci-full` / `ci-pr` and are not in the pre-push budget. See `feedback_ci_fast_pre_push.md`.

Auto-merge fires asynchronously when CI passes and required approvals are met (required approvals is 0 because GitHub blocks self-approval at the platform level for solo developers; discipline is preserved via PR-required + status checks + linear history + no-force-push). You do not need to poll the PR after `gh pr merge --squash --auto`. If you have follow-up work to do, do it. **Idle time is failure** -- see `feedback_idle_time_is_failure.md`.

If you need to follow a specific PR through to merge (cutover dependency, last-of-the-set), use `scripts/poll-pr.sh <pr> [interval]` which polls cleanly and single-line-outputs state changes. See `workflow_poll_pr_to_terminal_state.md`.

---

## 6. Tool gotchas and patterns

Hard-earned. Each item has burned at least one session.

### Bash: never pipe through `tail -N` (or `head`, `grep -m`) in backgrounded commands

`tail -N` buffers ALL stdin until the pipe closes. A backgrounded `cmd 2>&1 | tail -10` leaves the captured output file empty for the entire duration of `cmd`. Use `tee /tmp/<slug>.log` to keep streaming visibility:

```bash
# RIGHT
terraform apply -auto-approve 2>&1 | tee /tmp/tf-apply.log
# WRONG (file stays empty)
terraform apply -auto-approve 2>&1 | tail -20
```

This rule has bitten three times. See `feedback_never_pipe_through_tail_in_background_bash.md`.

### Polling pattern: foreground `until` loop, 10-30s cadence

Do not background a long-running poll. Run it in the foreground:

```bash
until [[ "$(aws rds describe-db-instances --db-instance-identifier X --query 'DBInstances[0].DBInstanceStatus' --output text)" == "available" ]]; do
  echo "$(date -u +%H:%M:%SZ) waiting..."
  sleep 20
done
```

ScheduleWakeup is for genuinely-no-signal waits (CI run minutes away, external queue), not for state changes the harness already notifies you about.

### One-shot Fargate task pattern for VPC-internal admin work

When you need to run admin work against a private-subnet resource (DB query, schema migration, user seed, ad-hoc data fix), do not stand up a bastion or flip the DB public. Use a one-shot Fargate task with the existing service's task definition and a `containerOverrides.command`:

```bash
aws ecs run-task \
  --cluster panakoes-dev \
  --task-definition panakoes-dev-auth:5 \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=DISABLED}' \
  --overrides '{"containerOverrides":[{"name":"auth","command":["node","dist/migrate.js"]}]}'
```

Then poll the task to STOPPED, check `containers[0].exitCode`, and read CloudWatch logs.

**CloudTrail caveat:** the RunTask request body is captured in CloudTrail Management Events for the default 90-day retention. Plaintext values passed in `containerOverrides.environment` ARE in those logs. For secrets, either (a) reference an AWS Secrets Manager ARN via `containerOverrides.secrets` (requires the task EXECUTION role to have read on that secret), or (b) use `aws ecs execute-command` to exec into a running task over SSM (session bodies are NOT logged to CloudTrail). The dev seed-admin script (`services/auth/scripts/seed-admin.sh`) uses the SSM exec path for this reason.

### AWS CLI streaming: always with `tee`, never with `tail`

Same as the Bash rule above, applied to AWS CLI. Long `aws ecs describe-tasks --waiter`, `aws rds wait`, `aws cloudformation deploy`: write to `tee /tmp/<slug>.log`, never pipe to `tail`.

### Terraform: backend lock contention

Parallel Terraform plans against the same S3 backend with DynamoDB-locking fight. Use `-lock-timeout=2m` so the second plan waits instead of failing. After the migration to `use_lockfile`, S3 conditional writes are the lock; same lock contention rules apply.

### `terraform-aws-modules/vpc/aws` 5.21.0 → ~> 6.0

The 5.21.0 pin resolved to a git SHA GitHub intermittently 500'd, blocking every `terraform init`. PR #236 bumped to `~> 6.0` (byte-identical interface). If you see `terraform init` failures on the VPC module, suspect this class of issue first.

### Docker buildx on WSL2: do not retry-loop

Buildx segfaults under WSL2 + Docker Desktop. The canonical bake path is the GitHub Actions image-bake workflow (PR #268). Local buildx is the offline fallback only. If it segfaults once, push and let CI bake.

### `make ci-fast` diffs HEAD~1..HEAD, not the working tree

`make ci-fast` (and the pre-push hook) inspect the COMMITTED diff (`HEAD~1..HEAD`), not the staged or unstaged tree. Running `make ci-fast` before committing reports only the prior commit's changes, which can look like a clean pass on a dirty tree. Commit first, then run. Discovered 2026-05-13 by the dep-upgrade-cve agent.

### nvm in non-interactive bash

nvm does not auto-load. If a script needs Node, source it explicitly:

```bash
source ~/.nvm/nvm.sh && nvm use --silent 22
```

### pnpm 10+ build-allowlist

Moved from `package.json` (`pnpm.onlyBuiltDependencies`) to `pnpm-workspace.yaml` (`onlyBuiltDependencies`). Check the warning surface on `pnpm install`; if a build is being skipped silently, fix the allowlist.

### Em-dashes are a hard rule

Phil's voice rule. No `--` (U+2014), no `--` (U+2013), in commit messages, comments, docs, PR bodies, code strings, anywhere. The pre-push hook catches it; CI catches it; the hook is the last line of defense, not the first. Scan every Edit/Write for `--` before submitting. This rule has been violated three times in a single session despite multiple memory files; the cost is each violation rebuilds CI from scratch.

---

## 7. The svelte-worker dispatch

For any Svelte/SvelteKit task in any Phil project:

1. Spawn the standing agent (or check if it is already alive in `svelte-work`): `Agent(subagent_type=svelte-worker, prompt=<spawn brief from workflow_svelte_worker_dispatch.md>)`.
2. Dispatch tasks via `mcp__claude-comms__comms_send(conversation="svelte-work", message="@svelte-worker <task brief>")`.
3. Interleave `mcp__claude-comms__comms_check` between user-facing replies so milestones, BLOCKED posts, and DONE posts surface to Phil. See `feedback_interleave_comms_check_with_output.md`.
4. When the worker reports DONE, do the post-subagent assessment.

For individual `.svelte` file edits within main-thread work (not handed to svelte-worker), use the official Svelte plugin's `svelte-file-editor` agent. The agent's description bakes in the full workflow (list-sections → get-documentation → svelte-autofixer → fix); the `svelte-task` prompt prepend is for unguided models and should NOT be used with this agent. Inline edits without the plugin miss paired Svelte 5 mixed-syntax issues. See `workflow_svelte_agent_and_lsp.md`.

---

## 8. Self-assessment ritual

Self-assessment is what keeps the workflow improving. Without it, the same friction recurs every session and the same wisdom evaporates at every compaction.

### When to run it

- **End of a session** before handoff to a fresh Claude (e.g. when Phil signals it).
- **After a major milestone** (a multi-PR feature ships, a migration completes, a thorny bug is solved).
- **When the same friction surfaces 2-3 times** in a session (the "3-strike workflow fix" reflex in `CLAUDE.md`).
- **When you spot wisdom that did not exist in any file before** (a tool gotcha, a pattern, a Phil preference).

### The ritual (10-20 minutes)

Answer each of these, in writing, in chat or directly into the relevant file:

1. **What worked.** What patterns, tools, or sub-agent dispatches produced clean outcomes? Note the conditions so the pattern can be repeated.
2. **What burned time.** Where did I spend more than I should have? Tool gotcha? Wrong agent type? Wrong scope estimate? Premature optimization? Inadequate Phil-alignment?
3. **What blocked.** What hard stops did I hit? External dependencies, CI flakes, Phil-decision-needed, unclear scope, missing tooling?
4. **What did Phil correct or validate.** Both directions. Corrections are easy to spot; quiet validations (Phil accepting an unusual choice without pushback) are easier to miss and equally valuable.
5. **What rule should be added or strengthened.** For each item above, would a rule have prevented the friction or amplified the win? Write it now. Add to `CLAUDE.md` if it is a project-wide rule, `WORKFLOW.md` if it is a working-rhythm rule, memory if it is a Phil-specific feedback.
6. **What memory should be created or updated.** Tool patterns, project state, workflow rituals, feedback. Slug the new file, write the frontmatter, add to `MEMORY.md`.
7. **What I will hand off to the fresh Claude.** One paragraph max: where we are, what is in flight, what is next, what is risky.

### Why this matters

A single 1M-context Claude session is not free. Every additional token of context burned reading old chat history is a tax on the user's weekly subscription budget. The handoff-to-fresh-Claude pattern is the cost-rational response, and it only works if the wisdom that lived in the long session has been distilled into files the fresh Claude can read in minutes.

The fresh Claude does NOT have your conversation history. It only has `CLAUDE.md`, this file, `MEMORY.md` and its linked topic files, and the codebase. If a lesson from the session is not in one of those three places, it is gone.

---

## 9. Failure modes to recognize

If you find yourself doing one of these, stop and re-route:

- **Idle polling.** Polling a background task that the harness will re-invoke you for when it completes. The Bash tool's `run_in_background: true` notifies on completion. Do other work; do not babysit.
- **`NO_VERIFY=1` push under time pressure.** The pre-push hook exists because we shipped an em-dash to main exactly this way. The hook is faster than recovering from CI failure; recovering from a main-branch em-dash is an order of magnitude slower than running the hook.
- **`git reset --hard` to make an obstacle go away.** Investigate the obstacle. WSL2 sometimes shows stale state; the right move is to understand it, not erase it.
- **"I will note that as a follow-up."** Cross-cutting findings get an immediate background sub-agent. See `feedback_always_delegate_cross_cutting_findings.md`. Follow-up notes parked in chat decay.
- **Implementing without alignment.** Exploratory questions get 2-3 sentences with a recommendation. Implementation starts only after Phil agrees.
- **Trusting a sub-agent's DONE message without verifying against authoritative state.** Read the run report. Read the diff. Read the live state.
- **Skipping the post-subagent assessment** because the agent looked successful. The hidden-quality-issues category exists for a reason.
- **Treating `MEMORY.md` as a memory.** It is an index. Detail goes in the topic files. `MEMORY.md` lines after ~200 get truncated at load time.

---

## 10. Handoff at context warning

When the harness signals you are nearing context exhaustion (or Phil signals "let's hand this off"):

1. Run the **self-assessment ritual** (section 8). Write down everything that should outlast this session.
2. Update `WORKFLOW.md` and `CLAUDE.md` with any new patterns or rules that emerged.
3. Drop a new memory file for anything that fits memory's scope (Phil preferences, project state, tool patterns, reference pointers).
4. Update `MEMORY.md` index entries for any new memory files. Keep entries one-line under ~150 characters.
5. Write a short handoff paragraph for the fresh Claude: where we are, what is in flight (open PRs, running background tasks, in-progress migrations), what is next, what is risky.
6. Mention any half-committed local state explicitly so the fresh Claude does not get surprised: uncommitted worktrees, running ECS tasks, scheduled wakeups, dispatched sub-agents.

A fresh Claude that reads `CLAUDE.md`, this file, `MEMORY.md`, and your handoff paragraph should be able to resume in ten minutes flat. That is the success metric.

---

## 11. Maintaining this file

`WORKFLOW.md` lives in the project root. Edit it in the same PR that proves a new pattern or fixes a recurring friction. Treat stale workflow docs as worse than missing ones because they teach the wrong reflex.

If you spot something in the codebase, in a memory file, or in chat that contradicts this file, fix the contradiction in the same session. The contradiction is the bug; do not work around it.

When a major architectural decision changes, update `CLAUDE.md`. When a working rhythm or pattern changes, update this file. When something Phil-specific changes, update memory. The three layers are intentional; do not collapse them.
