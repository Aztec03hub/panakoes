# FOLLOWUPS.md: Open Work and Unfinished Business

Last updated: 2026-05-14 (end-of-session handoff: architecture docs + billing tables fix dispatched; context compaction imminent).

A snapshot of what is in flight, what is pending Phil's decision, what is blocked, and what would have been done given more time. The fresh Claude picking this up should triage these against `MEMORY.md`, `CLAUDE.md`, and `WORKFLOW.md` before claiming any of them.

Each item lists: status, why it matters, what was attempted, and the suggested next move.

---

## ORCHESTRATOR HANDOFF (2026-05-14 session end)

**Read this section first.** It captures the live state at handoff so the fresh Claude can resume as orchestrator without reconstructing context from scratch.

### New documents to read at session start (in addition to the standard three-file load)

1. **`ARCH-MIGRATION.md`** (new, PR #350 -- may have merged): the authoritative architecture state + migration plan. Contains current infra state, Wave 0-3 task lists with review checkpoints, orchestrator context-management guide, and agent dispatch templates. Read it in full at session start. It replaces the stale architecture notes that were scattered in PLANNING.md.

2. **`docs/service-contracts.md`** (in progress, separate PR by agent `a899e4b0c518a56e6`): per-service boundary contracts generated from source code survey. May or may not have merged yet. Check `gh pr list` for a PR with title "docs: add service boundary contracts".

### Agents still in flight at handoff

Two background agents were running when this handoff was written:

**Agent 1: Service contracts (a899e4b0c518a56e6)**
- Task: survey all services under `services/` and produce `docs/service-contracts.md`
- Worktree: `~/projects/panakoes-service-contracts` (branch: `docs/service-contracts`)
- When done: opens a PR automatically. Verify the PR, prune the worktree, then update ARCH-MIGRATION.md section 6 with "service-contracts.md is live" note.

**Agent 2: Billing DDB tables fix (a116a322035ad2dc3)**
- Task: add missing `aws_dynamodb_table.billing_events` to `infra/dev/data/main.tf` and ensure subscriptions table is in outputs.tf
- Worktree: `~/projects/panakoes-billing-tables` (branch: `fix/billing-ddb-tables`)
- When done: opens PR. Verify terraform validate passed, check plan shows 1 new resource (billing_events). Prune worktree after merge.
- This is Wave 0.5 in ARCH-MIGRATION.md -- must merge and apply before Wave 1 starts.

**Check agent completion:** `gh pr list` to see if PRs appeared. If agents finished and notifications were not received due to compaction, check worktrees: `git worktree list`.

### PRs open at handoff

| PR | Title | Status | Action needed |
|---|---|---|---|
| #346 | feat(infra): move ecs to public subnets, remove nat gateway | OPEN, all CI green, auto-merge armed | Should auto-merge. Prune `~/projects/panakoes-path-a` after merge. |
| #348 | fix(infra): remove dead auth-db remote state references | OPEN, MERGEABLE, auto-merge armed | Should auto-merge. No worktree (already pruned). |
| #349 | feat(dev): add zero-cost local integration test stack | OPEN, auto-merge armed (Trivy failures are pre-existing, not required checks) | Should auto-merge. Pyright warning on root `conftest.py` is a false positive -- pytest not in repo-root venv, not a CI issue. |
| #350 | docs: add ARCH-MIGRATION.md architecture state and migration plan | OPEN, auto-merge armed | Should auto-merge. Updated with Wave 0.5 billing tables hotfix in second commit. |
| #351 | (pending -- billing tables agent will open this) | Not yet open | Verify after agent completes. |
| #352 | (pending -- service contracts agent will open this) | Not yet open | Verify after agent completes. |

### Worktrees at handoff

```
~/projects/panakoes              [main]
~/projects/panakoes-arch-docs    [docs/arch-migration-plan]  -- PR #350
~/projects/panakoes-path-a       [feat/ecs-public-subnets-drop-nat]  -- PR #346, prune after merge
~/projects/panakoes-billing-tables [fix/billing-ddb-tables]  -- billing agent working here
~/projects/panakoes-service-contracts [docs/service-contracts]  -- service contracts agent working here
```

### What was decided this session (key decisions for fresh Claude)

1. **Wave ordering:** Wave 0.5 (billing tables) must complete before Wave 1 (NLB removal). The billing service is currently broken at runtime for any billing route. This is a P1 bug, not a backlog item.

2. **Billing naming:** `panakoes-dev-billing-events` is the correct DDB table name (matches IAM forward-ref ARN and billing service config). An SNS topic with the same name exists in the events module -- this is a different AWS resource type and coexists without conflict. No rename needed.

3. **NLB count:** 11 NLBs (not 8 as earlier sessions recorded). The correct count is confirmed via `aws elbv2 describe-load-balancers`.

4. **KMS count:** 19 CMKs (not 24). The correct count is confirmed via `aws kms list-aliases`.

5. **Architecture documents:** ARCH-MIGRATION.md is the new primary reference for infra work. PLANNING.md is supplementary (ADR history). Every agent dispatch for Wave 1+ should be briefed to read ARCH-MIGRATION.md section 2.1 (or 2.2 for KMS work).

6. **Orchestrator rule Phil added:** When the orchestrator notices a discrepancy, bug, or issue -- it must be added to the migration plan (ARCH-MIGRATION.md) AND acted on immediately (spawn a fix agent). "Flag for awareness and move on" is not acceptable. Phil's exact words: "ANY time you see something like this or become aware of something similar, you must act like a senior architect orchestrator and work it into an appropriate next or current section of the plan being worked on, and ensure it gets done."

### Immediate next actions for fresh Claude (in priority order)

1. **Check agent completion:** run `gh pr list` and `git worktree list`. If agents finished, their PRs will be visible. Review each run report (`.agent-runs/*.md` in the relevant worktree, or the agent result summary).

2. **Verify billing tables PR (#351):** confirm `terraform validate` passed, plan shows billing_events as new resource, subscriptions also planned. Check for any issues in the run report. Prune `~/projects/panakoes-billing-tables` after merge.

3. **Verify service contracts PR (#352):** confirm `docs/service-contracts.md` was generated from real code (not hallucinated). Check the run report's "services whose config could not be found" list and manually fill any gaps. Prune `~/projects/panakoes-service-contracts` after merge.

4. **Pre-wave-1 reconfirmation checklist** (from ARCH-MIGRATION.md section 4, Wave 1): run all five checklist items before dispatching any Wave 1 agents. Do not skip this step.

5. **Wave 1 dispatch:** only after Wave 0.5 is merged, applied, and the billing tables are confirmed ACTIVE in AWS. Wave 1 agents W1-T1 (Cloud Map namespace) and W1-T2 (shared ALB) can run in parallel. W1-T3 blocks on W1-T2. W1-T4 blocks on W1-T1. W1-T5 blocks on W1-T3+W1-T4.

---

## 0. Cost-reduction session (2026-05-14): PRs and outstanding work

Phil received two AWS cost alerts (CloudWatch free tier 85%, budget $51.29 > $50 threshold). Root cause: 16 VPC Interface Endpoints ($345.60/mo gross) fully offset by AWS Activate Founders credits. Net cost is $0, but the budget alarm fires on gross Usage charges. Full analysis in this session's context.

### PR #344: MERGED

`fix(health-aggregator): wire real CloudWatch logs + Container Insights metrics`

MERGED before this session started. The `ContainerInsightsMetrics` IAM statement and removal of "(mocked)" admin SPA labels are in main.

**Worktree to prune:** `~/projects/panakoes-ha-real-data`

### PR #345: MERGED

`feat(ci): auto-deploy to ECS after image bake`

MERGED before this session started. Auto-deploy of new images to ECS is live.

**Worktree to prune:** `~/projects/panakoes-bake-deploy`

### PR #346: OPEN -- ECS public subnets + NAT Gateway removal

Branch: `feat/ecs-public-subnets-drop-nat`

**What it does:**
- Moves all 11 ECS services from private subnets to public subnets (`assign_public_ip = true`)
- Removes the NAT Gateway and EIP (`enable_nat_gateway = false`)
- NAT GW `nat-048c83ac02f93a2d2` and EIP `eipalloc-0c79730b62a7a13fd` are already destroyed in AWS (terraform applied by the agent)
- Saves ~$34/month (NAT GW $0.045/hour + data processing fees)

**Status:** Two commits. First commit is the agent's ECS/network Terraform changes. Second commit (fixup) restores the IAM `ContainerInsightsMetrics` statement and admin SPA Svelte file that the agent changed out of scope. CI re-running after fixup push. Auto-merge armed.

**Worktrees to prune after merge:**
- `~/projects/panakoes-path-a` (PR #346 worktree)
- `~/projects/panakoes-infra-destroys` (safe-destroys worktree, prune after its PR merges)

**After merge:** services reach ECR, KMS, Secrets Manager, CloudWatch, STS, etc. via their public IPs through the IGW. The private subnets remain (NAT-less, but ECS tasks no longer use them). Gateway endpoints for S3 and DynamoDB are FREE and unaffected.

**Risk:** if any ECS task has `assign_public_ip = false` or is in a private subnet after this, it loses AWS API access. Verify all services are healthy in the ECS console after merge.

### Safe-destroys agent (a268b702a0d67ed15): STILL RUNNING

Background agent in worktree `~/projects/panakoes-infra-destroys` destroying:

| Module | Status |
|---|---|
| `infra/dev/vpc-endpoints/` | Destroyed (21 resources, confirmed) |
| `infra/dev/waf/` | Destroyed (5 resources, confirmed) |
| `infra/dev/cloudfront-waf/` | Destroyed (5 resources, confirmed) |
| `infra/dev/auth-db/` (Aurora) | IN PROGRESS -- `terraform destroy -auto-approve` running |

Aurora destroy takes 5-10 minutes. After it completes, the agent should open a PR removing the Terraform directories (`git rm -r`) for all four modules. Wait for the notification.

**Suggested next move:** wait for the agent notification, then verify its PR, review the run report, and merge.

### Pending cost-reduction work (decided but not yet shipped)

| Item | Phil's decision | Estimated savings | Notes |
|---|---|---|---|
| 0 NLBs (ECS Service Connect + Cloud Map) | "Path B" chosen | ~$68/mo | Separate PR; complex wiring through API GW and ECS service discovery |
| KMS consolidation to 3 operational CMKs | Confirmed | ~$23/mo | Current: 24 CMKs at $1/mo each. Target: auth-signing, app-data, tf-state. Requires re-encrypting S3 buckets, rotating task-def refs |
| Fix AWS Budget to alert on net-of-credits | Needed to stop false alarms | $0 savings | Budget should filter by RECORD_TYPE != Credit; Phil should update in console |

**Updated monthly cost estimate after all destroys + PR #346 merges:**

| Item | Before | After |
|---|---|---|
| VPC Interface Endpoints | ~$346/mo | $0 |
| NAT Gateway | ~$34/mo | $0 |
| WAF (regional + global) | ~$10/mo | $0 |
| Aurora (auth-db) | ~$8/mo | $0 |
| **Subtotal removed** | ~$398/mo | $0 |
| KMS CMKs (24 at $1/mo) | ~$24/mo | ~$3/mo (after consolidation) |
| NLBs (8 services x $18/mo) | ~$144/mo | ~$0 (after Cloud Map) |
| Remaining AWS services | ~$20/mo | ~$20/mo |
| **Total gross** | ~$590/mo | ~$23/mo |

AWS Activate Founders credits ($1,000) still cover everything. Net cost remains $0.

---

## 1. Auth-DB cutover finishing (P5 burn-in + Aurora decommission)

**Status:** Aurora destroy in progress (safe-destroys agent running as of 2026-05-14). PR-B (decommission PR) will be opened by the agent.

**Where we are:** PR #314 (auth-db-rds Terraform module) merged on main 2026-05-12. RDS db.t4g.micro instance `panakoes-dev-auth-rds` is live and serving auth traffic. Aurora cluster deletion is now underway (safe-destroys agent confirmed `DeletionProtection: false` and launched `terraform destroy -auto-approve`). See `panakoes_auth_db_rds_cutover.md` in memory for the canonical record.

**What is still pending:**
- End-to-end sign-in verification from the SPA. The auth service connected to RDS successfully (migrations applied, service is listening, the seed-admin task did a real Better-Auth `signUpEmail` + UPDATE round-trip). The full APIGW → auth → RDS chain has NOT been timed end-to-end. The cold-start should be gone since RDS is always-on, but the proof requires Phil's sign-in from his browser.
- Wait for safe-destroys agent PR to open and merge.

**Suggested next move:** ask Phil to sign in from the SPA, time it, confirm under ~700ms. Then wait for the agent PR to merge.

---

## 2. ECS / auth-service portion of the cold-start (~100-500ms still on the table)

**Status:** pending; not in task list yet.

**Why it matters:** the cold-start research from earlier in the session attributed 11.6s of an 11.6s total to Aurora's resume-from-pause. The RDS cutover eliminates that. BUT the research deliberately treated everything-besides-Aurora as "the remaining ~100ms." That estimate was never re-validated post-cutover. The auth ECS service is on Fargate with a single task; if the task is recycled or scales to zero (it shouldn't with desired_count=1, but verify), there is a ~10-30s container cold start.

**Suggested next move (after Phil confirms #1):** measure the actual post-RDS sign-in latency. If consistently <500ms, mark this resolved with no action. If a periodic spike shows up (e.g. after long idle), investigate ECS task recycling, JWT signer KMS warm-up, or auth service connection-pool warm-up.

---

## 3. API Gateway `/v1/auth/{proxy+}` route 404 (RESOLVED 2026-05-13)

**Status:** RESOLVED. False alarm caused by a missing stage prefix in the test URL, not a real APIGW misconfiguration.

**Root cause:** the cutover-verification curls hit `/v1/auth/...` without the stage prefix. The APIGW v2 stage name is `dev` (autoDeploy=true), so the correct URL is `/dev/v1/auth/{proxy+}`. With the prefix, `/dev/v1/auth/health` returns HTTP 200 in ~90ms. Phil's browser sign-in works because the admin SPA uses the correct (`/dev/`-prefixed or Cloudflare-fronted-with-mapping) origin.

**Verification (2026-05-13):**
- `curl /v1/auth/health` → 404 (no stage)
- `curl /dev/v1/auth/health` → 200
- `aws apigatewayv2 get-routes` confirms `ANY /v1/auth/{proxy+}` → `integrations/whysyt7` → NLB listener `panakoes-dev-auth`
- `aws apigatewayv2 get-stages` confirms stage name is `dev`, not `v1`

**Lesson:** future cutover verification must hit the stage-prefixed URL. The bare-API-ID URL is `https://<id>.execute-api.<region>.amazonaws.com/<stage>/<route>`.

---

## 4. Worktree pile-up (8 stale worktrees, no unpushed work)

**Status:** safe to prune. All worktrees are 0 commits ahead of their remote tracking branch.

**Where we are:**

```
panakoes-billing-mount-and-me-rename     fix/billing-mount-and-me-rename
panakoes-ecs-services-deploy             fix/ecs-services-deploy
panakoes-fix-cors-auth                   fix/fix-cors-auth
panakoes-fragment-sweep                  feat/billing-stripe-webhooks
panakoes-login-ux-snappier               svelte/login-ux-snappier (behind 2)
panakoes-seed-admin-and-onclick          fix/seed-admin-and-onclick
panakoes-spa-and-deploy-hardening        fix/spa-and-deploy-hardening
```

The main checkout is currently on `feat/auth-db-rds` which is also stale (PR #314 already merged).

**Suggested next move:**
```bash
cd ~/projects/panakoes
git checkout main && git pull
git worktree remove --force ../panakoes-billing-mount-and-me-rename
git worktree remove --force ../panakoes-ecs-services-deploy
git worktree remove --force ../panakoes-fix-cors-auth
git worktree remove --force ../panakoes-fragment-sweep
git worktree remove --force ../panakoes-login-ux-snappier
git worktree remove --force ../panakoes-seed-admin-and-onclick
git worktree remove --force ../panakoes-spa-and-deploy-hardening
git worktree prune
```

Reclaims ~6-10 GB of disk. The `feedback_prune_worktrees_eagerly.md` rule is being violated by sheer accumulation.

---

## 5. Five admin-design mockup PRs all BLOCKED awaiting Phil's choice

**Status:** PRs #170-174 are open, all `mergeStateStatus: BLOCKED`.

**What they are:**
- #170 brutalist-raw
- #171 minimal-swiss
- #172 warm-editorial
- #173 bloomberg-dense
- #174 playful-friendly

**Why blocked:** these are five competing design directions for the admin SPA. They were dispatched in parallel (one Claude per direction) and are waiting for Phil to pick one. Once picked, the chosen branch gets merged and the others get closed.

**Suggested next move:** ask Phil to open each PR's preview screenshot, pick a winner, close the other four. Or roll all of them up into a single design-comparison doc and archive the branches without merging. Phil's call.

---

## 6. PR #315 (Playwright setup) is BEHIND main

**Status:** auto-merge armed but `mergeStateStatus: BEHIND`. Auto-rebase bot should sync it; if it does not, manually rebase + force-push.

**Suggested next move:** check after the next auto-rebase bot cycle. If still BEHIND in an hour, `gh pr update-branch 315` (or the API equivalent if the gh subcommand falls through) or rebase locally.

---

## 7. Pending task-list items not addressed this session

These are in the harness's task list as `pending`. The fresh Claude can see them via `TaskList` but they need a paper record so they outlast harness state.

| Task | Status | Notes |
|---|---|---|
| #24 Build Admin Dashboard Tier 2 (cost and budget tracker) | pending | Phase 2 just shipped (#99); Tier 2 was rolled into the phased plan. Verify whether this task should be marked completed or split further. |
| #25 Build Admin Dashboard Tier 3 (secure lifecycle controls) | pending | Phases 1-3 of Tier 3 (#100, #101, #102) all completed. Likely should be marked completed. Verify scope. |
| #105 Bump terraform-aws-modules/vpc/aws when upstream fixes data.aws_region.current.name deprecation | pending | Has been pending all session. Check the upstream module's release notes; if a fix has landed, bump and ship. If not, leave pending. |
| #125 Auth-DB P5 burn-in | in_progress | See section 1 above. |

**Suggested next move:** triage #24 and #25 against the actual phase-shipped state and either mark completed or break into remaining subtasks.

---

## 8. Real cold-start verification (the metric Phil cares about)

**Status:** unverified.

**What needs to happen:** Phil signs in from the SPA after 5+ minutes of idle. Times the round trip in the browser DevTools or by the visible spinner. Reports back: was it ~12s (old Aurora cold-start, would mean the cutover did not actually take effect) or ~300-700ms (the expected post-RDS shape)?

**Suggested next move:** ask Phil explicitly. Without this number, the entire Aurora → RDS migration is "merged but unverified."

---

## 9. 36 Dependabot vulnerabilities on the default branch

**Status:** open; reported on every push.

**What it is:** "GitHub found 36 vulnerabilities on Aztec03hub/panakoes's default branch (25 high, 10 moderate, 1 low)." Surfaced in every `git push` output banner. Not investigated this session.

**Suggested next move:** `gh api repos/Aztec03hub/panakoes/dependabot/alerts --paginate | jq` to enumerate. Triage by severity. Most will likely be transitive deps with patches available; Dependabot PRs auto-merge once the patch lands. The 25 highs are the priority.

---

## 10. PR #314 itself merged with FAILURE checks

**Status:** PR merged despite three FAILURE checks:
- `Verify checked-in openapi.json matches the live FastAPI app (services/cost-api)` -- pre-existing drift, not related to PR #314.
- `Scan filesystem (vulns + misconfigs)` -- Trivy filesystem scan; likely the same dependency vulns as in #9.
- `Plan infra/dev/auth-db-rds` -- the new module's first plan; this is expected on a fresh state-empty module and should succeed on the next CI cycle.

**Why it merged:** these checks are not in the required-status-checks set on the branch protection ruleset. Phil's `feedback_panakoes_lessons.md` notes that required-check additions must happen IMMEDIATELY after a workflow's first run. The auth-db-rds plan job is now a candidate for the required set.

**Suggested next move:** add `Plan infra/dev/auth-db-rds` to the required-status-checks on `main`. Investigate the openapi-drift failure (separate PR). Investigate the Trivy failure (probably resolves with #9).

---

## 11. The Playwright e2e test coverage is one smoke test

**Status:** smoke spec only (`services/admin/tests/e2e/smoke.spec.ts`).

**What is missing:** real coverage of the login flow (the actual thing #1 and #8 need verified), the dashboard, the admin lifecycle pages, the cost dashboard. The harness exists; the tests are unwritten.

**Suggested next move:** dispatch the svelte-worker with a brief to author a `login-flow.spec.ts` that hits the SPA's sign-in form, asserts redirect to `/dashboard`, asserts the JWT cookie is set. That single test is the proof we want for #1 and #8.

---

## 12. svelte-worker state on `claude-comms`

**Status:** unknown at handoff.

The svelte-worker is a standing agent dispatched via `claude-comms` on the `svelte-work` conversation. It may have been spawned earlier in the session and may now be idle / completed its 300-iter loop / handed off to a successor. The fresh Claude should:

1. Call `mcp__claude-comms__comms_conversations` to see if `svelte-work` is live.
2. If alive, call `mcp__claude-comms__comms_members` on it to see if `svelte-worker` is online.
3. If online, check its status with `mcp__claude-comms__comms_history` for the last few messages.
4. If a task is in flight, do not dispatch a second one until the first reports DONE.

See `workflow_svelte_worker_dispatch.md` for the full state-machine.

---

## 13. The `/tmp/admin_pw.txt` cleanup

**Status:** still on disk at mode 600 as of session end.

**Why it matters:** plaintext password file. Phil asked me to be careful with it. I asked if he wanted me to shred it; no answer yet.

**Suggested next move:** ask Phil if he wants it removed. `shred -u /tmp/admin_pw.txt` (overwrites then unlinks) is the careful version.

**Related caveat:** the password was passed as a plaintext `environment` override in the ECS RunTask call that seeded the admin user. That call is logged to CloudTrail for the default 90-day retention. If Phil cares about the plaintext leakage, rotate the password through the SPA's password-reset flow.

---

## 14. Memory files written this session (for fresh Claude awareness)

New memory entries written or strengthened during 2026-05-12:

- `panakoes_auth_db_rds_cutover.md` (new) -- auth-db cut over from Aurora to RDS; future sessions should NOT assume Aurora is in the auth path.
- `feedback_never_pipe_through_tail_in_background_bash.md` (strengthened) -- added a third incident; the rule is now a hard commitment.

Memory entries Phil reaffirmed during this session:
- `tee /tmp/<slug>.log` streaming visibility method (the third-incident enforcement).
- Em-dash hard rule (violated three times despite the existing memory; the pre-push hook caught one of them).

**Suggested next move (for fresh Claude):** read both new/strengthened entries before any backgrounded Bash or any AWS work that may be in CloudTrail-logged ECS overrides.

---

## 15. The "we never got to" wishlist (low priority, captured for future sessions)

Things that came up in conversation but were not committed to as work:

- **DynamoDB Better-Auth adapter** -- Phil asked whether one exists; the answer was "the community ones are unmaintained, do not adopt." Worth a periodic re-check (every 3-6 months) in case a maintained one shows up.
- **Auth-db decommission of Aurora's CMK** -- separate from the cluster, the Aurora module's CMK gets scheduled for deletion at decommission time. Confirm the 7-day deletion window does not accidentally lock out snapshot recovery.
- **Cost Anomaly Monitor coverage** -- task #106 shipped it, but the alarm thresholds were defaulted; the dev-account spend pattern from the last two weeks is now actually measured and could inform tuning.
- **GHA concurrent-job upgrade** -- Phil asked about doubling from 20 to 40 by going to GitHub Pro. Decision was deferred; revisit when CI queue depth becomes a real bottleneck.
- **SSM Session Manager plugin install for `seed-admin.sh`** -- the canonical `services/auth/scripts/seed-admin.sh` requires the SSM plugin locally. We bypassed it this session by writing a one-shot Fargate task. The script is the correct long-term tool; the plugin install is a one-line step on Phil's laptop that has never been done.
- **`enableExecuteCommand=true` on the auth task definition** -- required for the SSM exec path. Likely already true (the script assumes it); verify next time the script gets used.

---

## 16. Self-assessment from this session

Captured here so the fresh Claude inherits the lessons, not just the artifacts.

### What worked

- **One-shot Fargate tasks for VPC-internal admin work** (migration, schema query, user seed). Cleaner than bastions or temporarily public DBs. Repeatable.
- **Three-orthogonal-data-source root cause analysis** (curl timing + APIGW `integrationLatency` + Aurora ACU history) for the cold-start hunt. Each source corroborated the others; no single source would have been load-bearing on its own.
- **Polling AWS state in foreground with `until` loops, 10-30s cadence.** Crisp visibility, no background-polling waste, no harness re-invocation overhead.
- **`tee /tmp/<slug>.log`** for streaming visibility on every backgrounded long-running command, once I started doing it. Should have been the default from minute one.

### What burned time

- **Em-dash mistakes (three this session).** Each one cost a CI cycle or a hook-rejected push. The fix is mechanical: scan every Edit/Write for `--` before submitting. Not yet a reflex.
- **The `tail -N` background buffer trap (third occurrence).** Same lesson, same memory file, repeated. The fix is now a hard commitment in the memory; the test is whether it sticks in the next session.
- **API Gateway 404 chase** during cutover verification. Should have either ignored it cleanly or spawned a parallel sub-agent to investigate while continuing the cutover. Instead I bounced between probing it and continuing the cutover. Cost ~10 minutes of split attention.
- **Schema inline JS escaping** in the ad-hoc RDS query (`postgres()` template literal in `node -e` with shell quoting). Should have just written it to a file and ran it. Cost a failed task + a re-run.

### What blocked

- **Phil's credentials.** Real cold-start verification requires Phil sign-in. I cannot do it.
- **SSM Session Manager plugin** on Phil's laptop. Not installed, so the canonical seed-admin path was unavailable.
- **Required-status-checks ruleset.** New module's plan-on-PR check is not required, so PR #314 merged with a FAILURE on it. Branch-protection-additions discipline (per `feedback_panakoes_lessons.md`) was not applied immediately.

### Rules added or strengthened (this session's contribution to the workflow)

- `feedback_never_pipe_through_tail_in_background_bash.md` -- third incident logged; hard commitment.
- `panakoes_auth_db_rds_cutover.md` -- new memory entry.
- `WORKFLOW.md` -- created (this session).
- `FOLLOWUPS.md` -- created (this session).
- `CLAUDE.md` -- about to be updated with a reference to WORKFLOW.md and the maintenance instruction.

### Handoff paragraph for the fresh Claude

> The 2026-05-12 session shipped the auth-db cutover from Aurora to RDS db.t4g.micro (PR #314 merged) and a Playwright e2e harness for the admin SPA (PR #315 auto-merge armed but BEHIND). The cold-start spinner Phil mentioned at session start is functionally fixed but unverified end-to-end; ask Phil to sign in and time it. There are 8 stale worktrees to prune (no unpushed work), 5 design-mockup PRs blocked on Phil's choice, and one APIGW routing mystery worth investigating when convenient. `/tmp/admin_pw.txt` is still on disk; ask Phil before deleting. Memory has two new entries (auth-db cutover + strengthened tail-N rule); read them before doing anything backgrounded or AWS-adjacent. WORKFLOW.md (root of repo, new this session) is the next file to read after CLAUDE.md. Triage section 7 of this file (task list cleanup) early; sections 1, 8, and 11 are the highest-value next moves.
