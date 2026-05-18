# CLAUDE.md: Panakoes Project Conventions

This file is read by Claude Code on every session. It captures the durable conventions, locked decisions, and working patterns for the Panakoes project. Update it whenever a major decision changes; treat it as living documentation, not one-time setup.

## The three-file load order

Every session bootstraps by reading three files in this order:

1. **`CLAUDE.md`** (this file): WHAT the project is. Locked architectural decisions, discipline rules, sub-agent brief templates, off-limits directories.
2. **[`WORKFLOW.md`](WORKFLOW.md)**: HOW we work day to day. Session bootstrap, the work loop, sub-agent dispatch patterns, PR shipping flow, tool gotchas, the self-assessment ritual, failure modes. A fresh Claude that reads `CLAUDE.md` + `WORKFLOW.md` + `MEMORY.md` should be operationally effective within ten minutes.
3. **[`FOLLOWUPS.md`](FOLLOWUPS.md)**: WHAT is in flight or open. Snapshot of unfinished work, blocked PRs awaiting Phil's decisions, pending task-list items, the "we never got to" wishlist. Updated at session-end handoffs; pruned as items ship.

**Maintenance reflex (non-negotiable):**
- When a working rhythm or tool pattern changes, update `WORKFLOW.md` in the same PR (or a tight follow-up PR) that proves the pattern. Stale workflow docs teach the wrong reflex.
- When unfinished work or a blocked decision lands, update `FOLLOWUPS.md` so the next Claude inherits the queue.
- When a major architectural decision changes, update this file (`CLAUDE.md`).
- Run the **self-assessment ritual** (section 8 of `WORKFLOW.md`) at session-end, at major milestones, and after any 2-3-strike recurring friction. Distill the lessons into `WORKFLOW.md` / `CLAUDE.md` / memory before context-compaction or handoff. Wisdom that lives only in chat is wisdom that evaporates.

The three layers are intentional; do not collapse them. Locked decisions in `CLAUDE.md`, working rhythms in `WORKFLOW.md`, in-flight state in `FOLLOWUPS.md`.

---

## Project Snapshot

**Panakoes** is the cloud audio capture, transcription, and insights platform for LaFayette Labs LLC. The name is constructed Greek for "all-hearing", a parallel to Argus Panoptes ("all-seeing").

**Why it exists:**
1. First open-source project under LaFayette Labs LLC (filed 2026-04-23).
2. Doubles as the cloud backend for an upcoming AI wearable (a Plaud-killer, forking Omi).
3. Demonstrates AWS solutions-architect depth for Phil's job search portfolio.

**Home:** Public on Phil's personal GitHub at the `panakoes` repository. Mirrorable to a future LaFayette Labs GitHub organization.

**Domain:** [lafayettelabs.com](https://lafayettelabs.com) (LLC) and [panakoes.com](https://panakoes.com) (project), both registered at Cloudflare 2026-05-07.

---

## Locked Architectural Decisions

| Area | Decision | Why |
|---|---|---|
| Project name | Panakoes | Constructed Greek for "all-hearing"; parallels Panoptes |
| License | MIT | Matches Omi (which we fork for the wearable) |
| AWS region | us-east-1 | Cheapest, widest service coverage |
| IaC tool | Terraform | Industry standard; named in Scigon JD as preferred |
| Auth | Better-Auth | Modern, JWT-based, RBAC + step-up MFA support |
| Repo structure | Monorepo | Faster for week-1 build; polyrepo split later if scale demands |
| Frontend | SvelteKit on S3 + CloudFront | Phil's strongest frontend; AWS-native hosting |
| Languages | Python (most services), TypeScript (auth service) | Polyglot microservices; right tool for each job |
| Transcription mode | Dual-mode (async batch + live streaming) with pluggable Transcriber abstraction | Cost-efficient, model-agnostic |
| Async transcription | AWS Batch + EC2 g4dn.xlarge Spot, Whisper-large-v3 fp16 | Cheap GPU compute; pennies per audio hour |
| Streaming transcription | Session-spawned g4dn.xlarge Spot via custom AMI, faster-whisper-large + Silero VAD, streaming over WebSocket | Sub-second latency once warm |
| Long-audio chunking | Step Functions fan-out for files > 10 minutes | Bypasses Lambda 15-min ceiling |
| AI summarization | Claude Haiku 4.5 (default), Claude Sonnet 4.6 (paid-tier "deep summary" feature) | Cost discipline + tier differentiator |
| Payments | Stripe (Free / Pro $12mo / Team $30/seat min 3) | Real billing, competitive entry pricing |
| Observability | CloudWatch + AWS X-Ray (OpenTelemetry via ADOT) | AWS-native backends; vendor-neutral instrumentation |
| Logging | CloudWatch Logs (30-day) → S3 archive → Athena | Cheap, queryable at any timescale |
| Audit trail | DynamoDB custom log + AWS CloudTrail | App-level + AWS-API-level coverage |
| Testing | pytest + pytest-asyncio + httpx + testcontainers + moto (Python); vitest + msw (TS); Playwright (e2e) | TDD discipline; real DB in integration tests |
| Coverage gates | 80% on services; 100% on auth/billing/audit; 70% on infra | CI fails PR if below |
| MVP scope | v0.1 includes async + streaming both | Ambitious but high portfolio value (see SCOPE.md) |

For full architectural detail, rationale, and ADR-style decision records, see [`PLANNING.md`](PLANNING.md).

---

## Working Modes

### Default mode: Orchestrator-Delegation

Top-level Claude (orchestrator) decomposes work into focused sub-tasks, delegates each to a parallel sub-agent via the Agent tool, monitors progress, verifies output against acceptance criteria, integrates results, and surfaces only verified work to Phil.

**Every Agent tool call MUST:**

1. Tell the sub-agent to first read `CLAUDE.md` so it inherits all conventions before touching code.
2. Include a self-contained task brief with concrete acceptance criteria.
3. Reiterate the non-negotiables inline: Conventional Commits, CHANGELOG update, no secrets, branch-and-PR, TDD requirements.
4. Specify the agent's scope: which files it can touch, what it can install, whether it can push or only stage.
5. **Require the agent to emit a structured run report** at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per the format documented in `.agent-runs/README.md`. The report is the agent's final output; it is not optional.

**After a sub-agent returns, the orchestrator MUST:**

1. **Read the run report** at `.agent-runs/<run-file>.md`. Confirm `status: success` (otherwise re-delegate or escalate). Confirm files_created / files_modified match the actual diff via `git status` / `git diff`.
2. Check the diff matches the brief and stays in scope.
3. Confirm a `.changelog/<UTC-timestamp>-<slug>.md` fragment was added (or that the change qualifies for `docs:` / `chore:` skip per the workflow's exempt list).
4. Confirm commit messages follow Conventional Commits.
5. Confirm tests were added for new behavior and pass.
6. Run `gitleaks` against the diff.
7. Run lint and type-check on changed files.
8. Review the report's "Decisions Beyond the Brief" section. Surface any judgment calls that warrant Phil's attention before integration.
9. Confirm the report's "Rollback Procedure" is concrete and testable.
10. Reject and re-delegate if any gate fails. Don't rationalize; re-do.

**Why structured run reports matter (project convention):**
Sub-agents are first-class observable subjects, not opaque black boxes. The report is the agent's audit trail: what it built, what it decided, how to undo it. Combined with the orchestrator's verification, this turns AI-augmented development from "trust the diff" into "verify against the report." See `.agent-runs/README.md` for the required format and the orchestrator verification checklist.

### Parallel sub-agents MUST use git worktrees

Multiple sub-agents writing to the same git working tree at the same time is a known disaster pattern: they share `HEAD`, they share the staging index, and `git add -A` in one agent grabs another agent's untracked files. We hit this on day one and it cost ~30 minutes of recovery.

**The rule:** any sub-agent the orchestrator spawns to run concurrently with another sub-agent MUST be assigned a dedicated git worktree. Setup:

```bash
# Orchestrator does this BEFORE spawning the agent:
cd ~/projects/panakoes
git worktree add ../panakoes-<task-slug> -b feat/<task-slug> origin/main
```

**Important**: Always specify `origin/main` as the base. Without it, `git worktree add` uses the orchestrator's current HEAD, which silently propagates whatever feature commits the orchestrator happens to be on. We hit this on night two when a parallel agent's branch silently bundled an unrelated CLAUDE.md commit into a Terraform PR.

**Always create worktrees as a SIBLING of the repo root** (`../panakoes-<task-slug>`). On the Monday session, a worktree was once added from `services/admin/` as the CWD, which produced a nested worktree at `services/panakoes-<slug>` inside the main checkout. The orchestrator's `git add -A` then swept the nested worktree's `.git` pointer into a staged change. Anchor all worktree commands to the repo root (or use an absolute path) and never let CWD drift into a subdirectory before `git worktree add`.

**Scaling evidence:** the Monday session ran 8+ parallel sub-agents successfully with unique worktrees. The new bottleneck is disk: each worktree carries its own `node_modules/` + `.terraform/` + Python venvs, ~1 GB each in steady state. Run `git worktree remove --force ../panakoes-<slug>` (and `git branch -D <branch>` if local) immediately after the matching PR squash-merges. Stale worktrees pile up fast and Windows-side `du` numbers stop being amusing past ~10 GB.

Then the agent's brief includes:

```
WORKING DIRECTORY: ~/projects/panakoes-<task-slug>
All git operations and file edits happen in this directory only.
The branch `feat/<task-slug>` is already created and checked out for you.
After committing, push from this directory: `git push -u origin feat/<task-slug>`.
```

When the agent finishes and the PR merges, the orchestrator removes the worktree:

```bash
cd ~/projects/panakoes
git worktree remove ../panakoes-<task-slug>
git branch -D feat/<task-slug>  # local branch cleanup if needed
```

**Single-agent runs may skip worktrees** and use the main repo directly. The discipline is mandatory only when more than one agent is in flight at the same time.

### PR batching to reduce conflict surface

Dispatching N parallel sub-agents whose work touches the same files produces a cascading-rebase chain: each merged PR forces the next to rebase, rerun CI, and force-push, costing 5-15 min of agent time per rebase. The Sunday-Monday session shipped ~60 PRs and burned roughly 4 sequential rebase rounds on `infra/dev/ecs/variables.tf` alone across PRs #223, #229, #271, and #267. The fix is upfront batching, not downstream conflict resolution.

**Rule:** before dispatching N parallel agents, the orchestrator MUST compute pairwise file-set overlap and merge briefs that would touch the same files at the same lines.

1. Every agent brief declares an `EXPECTED FILES MODIFIED` section listing the file paths or globs the agent will touch (see the templates under "Common Sub-Agent Briefs").
2. Run `scripts/check-agent-overlap.py <brief>...` (or `--dir <briefs>/`) before dispatch. Non-zero exit = at least one brief pair overlaps; merge those briefs into one larger brief assigned to a single agent.
3. **Logical clustering still applies even when raw file paths differ.** All "deploy service X" work (`infra/dev/ecs/<svc>.tf` + image bake + IAM grant) goes to ONE agent. All "wire feature flag X into the SPA" goes to ONE agent. All "add ADR-NNN + runbook + memory" goes to ONE agent.
4. **Exception:** orthogonal-in-same-file edits (distinct service blocks appended to `infra/dev/ecs/variables.tf`) may stay parallel IF the agents are taught to use the keep-both-sides `sed` resolution from "Mechanical conflict resolution patterns". When in doubt, batch.
5. **Limit:** a batched brief MUST stay under ~4 hours of agent work. If batching pushes past 4h, keep the slices separate and accept the rebase cost; the escalation pattern from "Sub-agent escalation pattern" applies.
6. **Out of scope:** truly independent work (different services, different domains, no shared modules) stays parallel. Batching is not a default; it is triggered by detected overlap.

### Cross-cutting findings reflex

When a sub-agent's run report or summary flags an issue with scope beyond its own PR (broken upstream pin, env-var prefix mismatch across services, schema disagreement, blocked workflow, IAM over-grant, infra drift), the IMMEDIATE next action is to spawn a background sub-agent to fix it. Do not log it as a "follow-up later" note and move on; findings parked in chat context decay and the same class of bug reappears two weeks later.

The dispatch is mechanical: create a worktree off `origin/main`, write a tight one-finding-one-PR brief, fire as `run_in_background: true`, briefly acknowledge to Phil in the same message that wraps the original PR. Bundling unrelated findings into a "future cleanup PR" is the anti-pattern. PR #230 (broken VPC module SHA) is the canonical case where this reflex would have saved a round-trip; the agent only got spawned after Phil explicitly asked. Don't wait for the prompt.

### Defer-rather-than-half-ship

When a sub-agent realizes mid-run that the task scope is genuinely larger than one PR (multi-service refactor, several hours of cross-cutting work, deep test rewrites it cannot finish cleanly), it MUST escalate to the orchestrator with the three options written out: defer to backlog, decompose into N smaller PRs (with a proposed split), or push through past the original time box. Half-shipped PRs cost more than deferred ones because the partial state lands and rots.

The MFA enforcement task on the Monday session is the canonical example: the agent realized the cross-service rollout was a multi-PR sequence, escalated with the three options, Phil chose defer-to-backlog, and the agent closed cleanly without polluting main. Encourage that behavior; do not penalize an agent for stopping early when it surfaces a credible scoping concern.

### Off-limits directories

The directory `/mnt/c/Users/plafayette/Documents/Facebook/panakoes-hardware/` (Windows path: `C:\Users\plafayette\Documents\Facebook\panakoes-hardware`) is **NOT a Panakoes worktree**. It is a separate git repository for the LaFayette Labs wearable hardware, owned by Karl Long, worked on by a different Claude Code instance.

The naming is genuinely confusable: it reads identically to a Panakoes feature-branch worktree (`panakoes-billing-skeleton`, `panakoes-dev-batch`, etc.). Trust this distinction:

- `git worktree list` (run from THIS repo) **will not** include `panakoes-hardware`. Different repo, different history.
- `ls ~/Documents/Facebook/ | grep panakoes` **will** include it. That listing crosses repository boundaries; do not treat its members as worktrees.

**Hard rule for the orchestrator and every sub-agent:**

- Never `cd` into `panakoes-hardware/`. Never read, edit, or include in any agent's working scope.
- If a tool result, directory listing, or sub-agent's run report mentions `panakoes-hardware`, treat as not-yours and move past. Do not investigate, do not refactor, do not "include for completeness."
- The only exception is an explicit Phil instruction directing work into that repo. Default deny otherwise.

If you spawn a sub-agent and there is any chance its working directory could be ambiguous, state the working directory explicitly in the brief AND state `panakoes-hardware/` is off-limits.

### Mechanical conflict resolution patterns

When concurrent feature branches each append a CHANGELOG entry, git's three-way merge generates an "added by both sides" conflict even though the right resolution is always "keep both." Worse than the conflict itself: even with `merge=union` resolving the file content, every sibling PR was marked `DIRTY` on every merge, stalling GitHub auto-merge. This was the dominant PR-backlog churn source on the 2026-05-08 drain.

**Rule:** the canonical going-forward pattern is per-PR fragment files under `.changelog/` (see the "CHANGELOG and README" section). Each PR drops one `.changelog/<UTC-timestamp>-<slug>.md`; PRs no longer share a file, so the `DIRTY` cascade is eliminated. `.gitattributes` keeps `CHANGELOG.md merge=union` as a belt-and-suspenders fallback for direct edits to the assembled file (e.g. backport typo fixes). Do not extend `merge=union` casually to other files.

If you encounter a similar mechanical conflict on a different additive log file (e.g., a planned future activity log), evaluate whether `merge=union` semantics are correct (additive only, no reordering, no de-duplication) before adding the file to the gitattributes list.

For cases where merge=union does not apply but the conflict is still mechanical (e.g., a list of services in `infra/README.md` getting an entry from each terraform module), the pattern is: `for f in $(git diff --name-only --diff-filter=U); do sed -i '/^<<<<<<<\|^=======\|^>>>>>>>/d' "$f"; done` to keep both sides verbatim. Use this only when both sides' additions are truly independent and the file is an ordered list of independent items.

### Principal-engineer reflex: 2-3 strikes = workflow fix

When the same friction surfaces 2-3 times in a session, stop fixing the symptom and fix the workflow. Examples that produced lasting fixes during night two:

- `gh pr update-branch <pr>` is not a real subcommand in our `gh` version, falls through to the help menu silently. **Fix:** use `gh api -X PUT repos/<owner>/<repo>/pulls/<pr>/update-branch`, or rebase + force-push locally.
- CHANGELOG check failed every CLAUDE.md edit. **Fix:** added `CLAUDE.md`, `PLANNING.md`, `SCOPE.md` to the workflow's exempt list (PR #26).
- Parallel CHANGELOG additions kept producing conflicts on rebase. **Fix:** `.gitattributes` with `CHANGELOG.md merge=union` (PR #27).
- Worktrees inherited orchestrator's current HEAD as base. **Fix:** `origin/main` explicit base in the setup command (this CLAUDE.md update).
- Em-dash hit the admin SPA via a `NO_VERIFY=1` push when the pre-push hook was bypassed under time pressure (PR #232). Every later PR that ran `make ci-pr` failed on the same line. **Fix:** PR #242 removed the em-dash; reinforced rule that `NO_VERIFY=1` is an emergency escape hatch, not a workflow.
- `terraform-aws-modules/vpc/aws` v5.21.0 resolved to a git SHA that GitHub intermittently 500'd, blocking every `terraform init` against `infra/dev/network`. **Fix:** PR #236 bumped the pin to `~> 6.0` (byte-identical interface), removing the broken-SHA dependency.
- Docker buildx segfaulted twice in 24h on WSL2 + Docker Desktop during local image bakes. **Fix:** PR #268 introduced a GitHub Actions image-bake workflow; local buildx is now the offline fallback, not the primary path.
- Auth-service image needed a fresh rebake after the `0002_add_session_revoked_at.sql` migration PR merged (the registered task definition's baked image predated the SQL file, so the migrator silently skipped the new column). **Fix:** PR #244 tracked the cross-cutting deploy dependency and codified the rebake-on-migration step in the auth-db runbook.

When you spot a recurring friction, write the fix down here AND submit it as its own small PR. Future-you will thank present-you.
- Terraform PRs that include resource replacement or destroy actions require the `replace-allowed` GitHub label or the `Plan infra/dev/<module>` CI gate fails. The pre-ship CI gate (`terraform plan`) on PRs shows this. **Fix:** Orchestrator must add `replace-allowed` to the PR immediately before or after creation whenever the planned Terraform changes include `destroy` or `replace` entries. Infra sub-agents must check their plan output for replacements and note it in their run report so the orchestrator knows to add the label. The `gh api -X POST "repos/Aztec03hub/panakoes/issues/<pr>/labels" -f labels[]="replace-allowed"` command is the correct invocation (not `gh pr edit --add-label`). This came up on W1-T3 (ALB listener rule condition type change) and W1-T4 (ECS task definition replacement + ECS service updates).

### Direct mode (exception)

When Phil explicitly says "you do this" or the task is too small / too tightly coupled / inherently sequential to delegate (single-line config edits, decision conversations, file reads for orientation), Claude does the work directly with identical discipline.

Default is delegation; direct mode is the explicit exception.

---

## Discipline Rules

### Commits and Pull Requests

- **Conventional Commits** for every commit. Format: `type(scope): subject`. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`, `build`, `security`.
- **Branch from `main`.** Naming: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `security/<topic>`, `ci/<topic>`.
- **Every change ships via PR**, even self-merged. CI runs on every PR.
- **Squash-and-merge** to `main`; main stays linear.
- **No force-push to `main`**, ever, except a documented secret-scrub emergency.
- **No `git reset --hard` on shared history.** Rollback via `git revert`.

### Releases and Tagging

- **SemVer tags** on meaningful checkpoints: `vMAJOR.MINOR.PATCH`.
- Bump rules: MAJOR for breaking, MINOR for new features, PATCH for fixes.
- Every tag triggers a GitHub Release with auto-generated notes from PRs since the prior tag.

### CHANGELOG and README

- **Per-PR `.changelog/` fragments are the canonical pattern.** Every PR with user-visible product impact drops exactly one file at `.changelog/<UTC-timestamp>-<short-slug>.md`. Generate the timestamp with `date -u +%Y%m%dT%H%M%SZ`. Fragment format (YAML frontmatter + Markdown body):

  ```markdown
  ---
  category: Added
  ---

  - `services/admin`: short user-visible description.
  ```

  Required: `category` is one of Added, Changed, Deprecated, Removed, Fixed, Security (Keep a Changelog). Required unless the PR is docs-only (see exempt list in `.github/workflows/changelog-check.yml`).
- **Why fragments over the monolithic CHANGELOG.md:** the old "every PR appends to CHANGELOG.md" pattern marked every sibling PR as `DIRTY` on every merge (even with `merge=union` resolving the file content), which stalled GitHub auto-merge waiting for manual rebases. Per-file fragments eliminate the shared-write contention. See `feedback_changelog_fragments_not_monolith.md` in memory.
- **At release time:** `scripts/assemble-changelog.sh --version vX.Y.Z --prune` collects all `.changelog/*.md` fragments, groups by category in canonical Keep a Changelog order, prepends a versioned block to `CHANGELOG.md`, and deletes the fragments. Use `--dry-run` to preview. See `.changelog/README.md` for the full release flow.
- The `CHANGELOG.md merge=union` line in `.gitattributes` stays as a belt-and-suspenders fallback; direct CHANGELOG.md edits still pass the CI gate.
- **README.md** updated when: a new feature ships, setup steps change, the tech stack changes, a new top-level service is added, or a breaking architectural change lands.
- A GitHub Action gate fails the PR if source code changed but no `.changelog/*.md` fragment was added (and `CHANGELOG.md` was not directly edited). Skippable for `docs:` / `chore:` PRs via label.

### No Secrets, Ever

- Public repo. Anyone can read the code. **No secrets in source code, ever.**
- All API keys, DB passwords, signing secrets, webhook secrets read from environment variables or AWS Secrets Manager / SSM Parameter Store at runtime.
- `.gitignore` blocks `.env`, `.env.*`, `*.tfstate`, `*.tfstate.backup`, `.terraform/`, `*.pem`, `*.key`.
- `gitleaks` pre-commit hook scans every commit; rejected if a secret is detected.
- GitHub secret scanning + push protection enabled at the repo level (free for public repos).
- Terraform state is remote (S3 + KMS-encrypted + DynamoDB lock). State files never in repo.
- GitHub Actions to AWS via OIDC federation. No long-lived AWS access keys anywhere.

### Testing (TDD where required)

- **Write the test first** for any new business logic, security path, or bugfix.
- **Unit tests** for individual functions and modules.
- **Integration tests** for cross-service or cross-component changes; use `testcontainers-python` for real Postgres / Redis instances. Do NOT mock the database.
- **End-to-end tests** for full user flows via Playwright against a deployed dev environment.
- Coverage gates enforced in CI: 80% on application services, 100% on auth/billing/audit paths, 70% on infrastructure-adjacent code.

### Sub-agent escalation pattern

If a sub-agent's task realistically exceeds ~4 hours of cross-service work once it starts digging in, ESCALATE to the orchestrator rather than half-ship. Surface three options in the escalation message: (1) defer the whole task to backlog, (2) decompose into a proposed list of smaller PRs, (3) push through past the time box. The orchestrator surfaces those options to Phil; Phil picks. The MFA enforcement task on Monday did this correctly (deferred per Phil's call) and is the reference precedent. A clean stop with a clear escalation is a successful run, not a failed one; do not penalize agents for this behavior.

### PR title format is the agent's responsibility, not the workflow's

Agents MUST format their PR titles per Conventional Commits at dispatch time: `type(scope): subject` with type from feat/fix/chore/docs/refactor/test/style/ci/perf/build/security and the subject in lowercase. The `auto-recover-pr.yml` workflow (`.github/workflows/auto-recover-pr.yml`) attempts a heuristic rewrite when a non-conforming title trips the `Validate Conventional Commits format` check, but the heuristic is conservative and will not cover every shape. The workflow is a safety net to catch stragglers, not a substitute for getting it right at dispatch.

The same workflow auto-handles a small catalog of mechanical check failures: Trivy CVE detection (downloads the log, posts the top CVE row), `Test services/<name>` failures (posts failing test names + last 50 log lines), `Plan infra/dev/<module>` failures (posts the error block), and CodeQL self-trip flagging (applies `codeql-self-trip-ack`). When a `Verify .changelog fragment` check fails the workflow comments with the fragment-creation recipe and applies `needs-changelog-fragment`. None of these auto-handlers excuse the originating agent from doing it right the first time; the time cost of a CI cycle is real.

### Phil's Voice Rules

- **No em-dashes**, ever. Use commas, periods, parentheses, semicolons. (Hard rule across all of Phil's work.)
- **Direct, concise communication** in commit messages, PR descriptions, doc copy.
- **No marketing fluff** in user-facing copy. Concrete and specific.

---

## Document Map

| File | Purpose |
|---|---|
| [`README.md`](README.md) | Public-facing entry point: what, why, how to use |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog format; updated on every meaningful change |
| [`CLAUDE.md`](CLAUDE.md) | This file; project conventions for Claude Code |
| [`WORKFLOW.md`](WORKFLOW.md) | How Claude and Phil work day to day: bootstrap, work loop, sub-agent patterns, self-assessment ritual, failure modes |
| [`FOLLOWUPS.md`](FOLLOWUPS.md) | In-flight work, blocked PRs, pending decisions, and the session-handoff state for the next Claude |
| [`PLANNING.md`](PLANNING.md) | Architecture decisions, rationale, evolution log (running ADR journal) |
| [`SCOPE.md`](SCOPE.md) | MVP scope vs deferred-to-phase-2 |
| [`SECURITY.md`](SECURITY.md) | Vulnerability disclosure, security model, threat model summary |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute, branch/commit conventions, dev setup |
| [`LICENSE`](LICENSE) | MIT license text |
| `docs/architecture.md` | Detailed architecture (services, data flow, AWS map) |
| [`docs/service-contracts.md`](docs/service-contracts.md) | Per-service boundary contracts: API routes, env vars, AWS resource names, DDB schemas, SQS message shapes. Read before dispatching any agent that crosses service boundaries. |
| `docs/aws_activate_application.md` | Draft content for AWS Activate Founders application |
| [`ARCH-MIGRATION.md`](ARCH-MIGRATION.md) | Architecture state (current + target), dev vs prod differences, migration waves with task lists and review gates, orchestrator context-management guide. Read at every session start alongside CLAUDE.md. |
| `services/<name>/README.md` | Per-microservice docs |
| [`services/_template/README.md`](services/_template/README.md) | Template skeleton every new Python service copies; documents the pyproject + test + Dockerfile pattern |
| `infra/README.md` | Terraform layout and bootstrap process |
| `.agent-runs/README.md` | Required format for sub-agent run reports + orchestrator verification checklist |
| [`.claude/agents/`](./.claude/agents/) | Project-scoped agent definitions (auto-discovered by Claude Code). Each `.md` file is a long-running, dispatchable agent with its own contract; see `.claude/agents/dependency-updater.md` for the canonical example. Distinct from `.agent-runs/` (which holds ephemeral per-run reports) and from the inline "Common Sub-Agent Briefs" templates further down in this file (which are one-shot briefs). |

---

## Project Agents

Long-running, file-defined agents live under [`.claude/agents/`](./.claude/agents/). Each is a Markdown file with YAML frontmatter that the orchestrator can dispatch via the Agent tool by name. Use these when a job recurs across sessions and benefits from a permanent, version-controlled contract rather than a one-off inline brief.

| Agent | Purpose | Typical trigger |
|---|---|---|
| [`dependency-updater`](.claude/agents/dependency-updater.md) | Audit + bump packages across Python (uv), TypeScript (pnpm), Terraform, GitHub Actions; preempt Dependabot; handle major-version migrations with mechanical refactors; local-first verification before push. | User asks "update our deps", a Dependabot security alert lands, or a scheduled audit run fires via `/schedule` or `/loop`. |

Add new agents by dropping a new `.md` file in `.claude/agents/` (frontmatter + system prompt), following the structure of the existing files. Validate with `bash plugin-dev/skills/agent-development/scripts/validate-agent.sh <path>` (mind the script's stale `<example>` warning; the canonical format is the prose summary + body "When to invoke" section, matching the skill's own example agents).

---

## Tooling Map

- **gitleaks**: pre-commit secret scanner
- **terraform**: infrastructure as code
- **awscli**: AWS API access
- **packer**: custom AMI builder for the GPU streaming AMI
- **pnpm**: TypeScript package manager (Better-Auth service, frontend); pinned to 11.0.8
- **biome**: TypeScript lint + format (replaces eslint + prettier in TS services)
- **hono**: TypeScript web framework (auth service and any future TS HTTP services)
- **better-auth**: authentication library (auth service)
- **drizzle-kit**: Drizzle ORM CLI for schema migrations (TS services)
- **vitest**: TypeScript tests
- **uv**: Python dependency manager (services); `~/.local/bin/uv`
- **make**: repo-root cross-service targets (`make setup`, `make test`, `make lint`, `make typecheck`, `make check`)
- **pytest** + **pytest-asyncio** + **pytest-cov**: Python tests
- **Playwright**: end-to-end tests
- **gh** (GitHub CLI): PR creation, repo management
- **whois**: domain availability checks (see workflow_domain_availability_check memory)

Install commands and version pinning live in setup scripts under `scripts/`.

**Docker buildx on WSL2 is fragile.** It segfaulted twice in 24h during the Monday session on Phil's box (WSL2 + Docker Desktop + buildx 29.x). The canonical image-bake path is now the GitHub Actions workflow introduced in PR #268 (GHA runner + OIDC to ECR, no long-lived creds, no local segfaults, matrix-parallel across services). Local `docker buildx build` is the offline fallback only; if it segfaults, do NOT retry-loop, push the change and let CI bake.

**`.agent-runs/` is gitignored by design.** Sub-agents write run reports there per `.agent-runs/README.md`; the only file under version control in that directory is the README itself. Reports are local-only audit telemetry; they persist across sessions on Phil's machine but never reach the public repo. Multiple sub-agents on the Monday session asked whether to commit their report; the answer is always no. Anything from a report that deserves permanent record gets copied into `CHANGELOG.md`, `PLANNING.md`, or a runbook before the report is pruned.

---

## Common Sub-Agent Briefs

The canonical starting point for every sub-agent dispatch is [`docs/templates/agent-brief.md`](docs/templates/agent-brief.md). Copy that file's body into the Agent tool's `prompt` field, fill in the placeholders (working directory, base commit, prerequisite reading list, acceptance criteria, push/PR toggles), and dispatch. The template encodes the full discipline contract (Conventional Commits, em-dash ban, mandatory changelog fragment, local-first verification, mandatory progress log + run report) in one place so every dispatch picks it up by default.

The inline templates below are pre-filled examples of that skeleton for the most common patterns (service implementation, test-writing, Terraform). Keep them in sync with the canonical template if the skeleton evolves; they remain useful as worked examples. The Wave-N dispatch briefs in [`ARCH-MIGRATION.md`](ARCH-MIGRATION.md) section 7 are additional worked examples scoped to in-flight architecture migrations.

When delegating recurring patterns, use these templates as starting points. They evolve as we learn what works.

### Implementing a microservice

```
You are implementing the [SERVICE_NAME] microservice for Panakoes.

PREREQUISITE: First read ~/projects/panakoes/CLAUDE.md and ~/projects/panakoes/.agent-runs/README.md. Follow ALL conventions described therein.

TASK: [specific task description]

ACCEPTANCE CRITERIA:
- [test 1]
- [test 2]
- [test 3]

DISCIPLINE (non-negotiable):
- TDD: write the failing test first, then make it pass.
- Conventional Commits with appropriate type (feat / fix / refactor / etc.).
- **YOU MUST DROP A FRAGMENT FILE AT `.changelog/<UTC-timestamp>-<short-slug>.md`** with YAML frontmatter `category: Added|Changed|Deprecated|Removed|Fixed|Security` and a terse user-visible Markdown bullet body. Generate the timestamp with `date -u +%Y%m%dT%H%M%SZ`. See `.changelog/README.md` for the exact format. The changelog-check CI gate will fail and the PR will not merge if this fragment is missing. Exception: PRs scoped to `docs/*`, `.github/*`, `scripts/*`, or `CLAUDE.md`/`PLANNING.md`/`SCOPE.md` are exempt by the workflow's exempt list.
- All secrets via env vars or AWS Secrets Manager; no hardcoded values.
- Coverage minimum: 80% on services (100% on auth/billing/audit code).

SCOPE:
- Files you may modify: services/[name]/, tests/[name]/, `.changelog/<timestamp>-<slug>.md` (new fragment), services/[name]/README.md.
- Do NOT modify: infrastructure code, other services, top-level docs (other than dropping a single `.changelog/` fragment).
- Work on a feature branch named feat/[name]-<short-desc>; do not push to main.

EXPECTED FILES MODIFIED (declare upfront so the orchestrator can detect overlap):
- services/[name]/**
- tests/[name]/**
- CHANGELOG.md

REQUIRED FINAL OUTPUT: Write a structured run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per the format in `.agent-runs/README.md`. The report has YAML frontmatter (run_id, agent_description, timestamps, status, files_created/modified/deleted, commits_made, verification metrics) and a markdown body with sections: Summary, What I Built, Decisions Beyond the Brief, Issues Encountered, Suggestions for Follow-up, Rollback Procedure. Use UTC timestamps in ISO 8601 format. The report is the orchestrator's audit trail; treat it as a first-class deliverable.

When done, return a brief summary (under 200 words): the path of your run report, confirmation of test results and coverage, and any items in the report that need Phil's review before integration.
```

### Writing tests for existing code

```
You are adding tests for [MODULE / SERVICE] in Panakoes.

PREREQUISITE: First read ~/projects/panakoes/CLAUDE.md and ~/projects/panakoes/.agent-runs/README.md.

TASK: Add [unit / integration / e2e] tests for [target] to bring coverage to [target percent].

ACCEPTANCE CRITERIA:
- All new tests pass locally.
- Coverage on [target file/module] reaches [percent].
- No flaky behavior; deterministic across 10 consecutive runs.
- Integration tests use testcontainers for real DB (no mocking the DB).

DISCIPLINE: same as service-implementation brief.

SCOPE: tests/[area]/ and the target file/module if minor refactors are required for testability.

EXPECTED FILES MODIFIED (declare upfront so the orchestrator can detect overlap):
- tests/[area]/**
- [target file/module path if refactored for testability]
- CHANGELOG.md

REQUIRED FINAL OUTPUT: Run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per `.agent-runs/README.md`.
```

### Updating Terraform

```
You are modifying Terraform infrastructure for Panakoes.

PREREQUISITE: First read ~/projects/panakoes/CLAUDE.md, ~/projects/panakoes/.agent-runs/README.md, and infra/README.md.

TASK: [specific infra change]

ACCEPTANCE CRITERIA:
- terraform fmt clean.
- terraform validate clean.
- terraform plan shows only the intended change (no drift, no unintended modifications).
- IAM policies follow least-privilege; no wildcards on resource ARNs.

DISCIPLINE:
- Conventional Commits with type `chore(infra)` or `feat(infra)` as appropriate.
- **YOU MUST DROP A FRAGMENT FILE AT `.changelog/<UTC-timestamp>-<short-slug>.md`** with YAML frontmatter `category: Added|Changed|Deprecated|Removed|Fixed|Security` and a terse user-visible Markdown bullet body. Generate the timestamp with `date -u +%Y%m%dT%H%M%SZ`. See `.changelog/README.md` for the exact format. The changelog-check CI gate will fail and the PR will not merge if this fragment is missing. Exception: PRs scoped to `docs/*`, `.github/*`, `scripts/*`, or `CLAUDE.md`/`PLANNING.md`/`SCOPE.md` are exempt by the workflow's exempt list.
- **`replace-allowed` label:** If your Terraform plan includes any resource replacements or destroys, note this explicitly in your run report under "Decisions Beyond the Brief." The orchestrator will add the `replace-allowed` GitHub label before CI runs (`gh api -X POST "repos/Aztec03hub/panakoes/issues/<pr>/labels" -f labels[]="replace-allowed"`); without it, the `Plan infra/dev/<module>` CI gate will fail with "Destructive Terraform plan without replace-allowed label."
- Update infra/README.md if a new module is introduced.

SCOPE: infra/ directory only; do not modify application code.

EXPECTED FILES MODIFIED (declare upfront so the orchestrator can detect overlap):
- infra/[module]/**
- infra/README.md (if a new module is introduced)
- CHANGELOG.md

REQUIRED FINAL OUTPUT: Run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per `.agent-runs/README.md`.
```

---

## Updates to This File

Update CLAUDE.md in the same PR that lands a major architectural decision change. Treat as living documentation.
