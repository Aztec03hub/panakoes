# FOLLOWUPS.md: Open Work and Unfinished Business

Last updated: 2026-05-19 04:30 UTC (telemetry implementation agent dispatched; W2 KMS migration arc fully applied; cost steady-state down to ~$67/mo gross).

A snapshot of what is in flight, what is pending Phil's decision, what is blocked, and what would have been done given more time. The fresh Claude picking this up should triage these against `MEMORY.md`, `CLAUDE.md`, and `WORKFLOW.md` before claiming any of them.

Each item lists: status, why it matters, what was attempted, and the suggested next move.

---

## ORCHESTRATOR STATE (2026-05-19 04:30 UTC, telemetry implementation in flight)

**Read this section first; older sections below are historical context.**

### Session arc 2026-05-19 (continuation of the 2026-05-18 marathon)

Massive cascade this session arc. ~15 PRs merged, telemetry design fully shipped through 3-stage review cycle, Wave 2 KMS migration arc applied end-to-end including 3 deferred-task follow-ups (RDS, Backup, ECR) + 1 cross-cutting extension (T4-ext) + the never-applied PR-281 stub-to-real Lambda swap. Phil granted full tf-apply authority mid-session.

### In-flight as of 2026-05-19 04:30 UTC

| Item | State | Next |
|---|---|---|
| Telemetry IMPLEMENTATION agent `ad2d31` | RUNNING in `panakoes-telemetry-impl` | Will produce trace-shim + flusher + SQLite schema + .claude/settings.json + bench + tests; ~3-4h |
| pr-monitor (task `bpd0dtv7i`, v6) | RUNNING in persistent mode | Keep until session end |
| 16 ECS services post-W2-T3 v2 cutover | All COMPLETED rollout to v2 images | No action; monitor health |
| auth-db RDS v1+v2 parallel | Both running | DSN cutover deferred (W2-T7 follow-up) |
| Backup parallel-vault | Both running | Old vault retire after 30/365-day burn-in (W2-T7 follow-up) |

### W2 KMS migration status (2026-05-19, post-arc)

| Task | State | Notes |
|---|---|---|
| W2-T1 (consolidated CMKs) | MERGED + APPLIED | PR #365 |
| W2-T2 (S3 SSE) | MERGED + APPLIED | PR #405 |
| W2-T3 (Secrets/SQS/SNS) | MERGED + APPLIED | PR #405 |
| W2-T3 (Backup parallel vault) | MERGED + APPLIED | PR #406 |
| W2-T3 (ECR v2 repos + ECS task-def flip) | MERGED + APPLIED | PR #409 + manual image-copy via `docker buildx imagetools create` (see `reference_ecr_image_copy_recipe.md` memory) |
| W2-T4 (CloudWatch Logs KMS) | MERGED + APPLIED | PR #405 |
| W2-T4 extension (3 more log-group CMKs + step-functions bug fix) | MERGED + APPLIED | PR #408 + manual api-gateway-ws apply (recovered never-applied PR-281 state) |
| W2-T5 (RDS snapshot+restore migration) | MERGED + APPLIED | PR #407 + PR #410 fix + manual apply; v2 instance live, DSN cutover deferred |
| W2-T6 (ECS IAM grants pickup via remote-state) | MERGED + APPLIED | PR #405 + manual iam apply |
| W2-T7 (retire 15 old per-service CMKs after burn-in) | DEFERRED to next session | After 24h with no CloudWatch errors |

### Backlog (priority order)

1. **Telemetry implementation agent (in flight)** - wait for completion notification; verify; merge.

2. **W2-T7 CMK retirement** - schedule deletion of 15 old per-service CMKs via `aws kms schedule-key-deletion --pending-window-in-days 7` after 24h burn-in confirms no service errors. Affects: per-service S3/Secrets/SQS/SNS/RDS/ECR/Backup CMKs that were superseded by `panakoes/app-data` + `panakoes/logs`. Orchestrator-only (CLI; no Terraform).

3. **auth-db RDS DSN cutover (W2-T5 burn-in)** - swap `panakoes-dev/postgres-auth-db-password` DSN value via `aws secretsmanager put-secret-value` to point at v2 endpoint. Brief auth outage (~5 min). Then W2-T7 retire v1 instance after burn-in.

4. **Backup vault retirement (post-burn-in)** - after 30d daily + 365d monthly parity, retire old vault + selection + plan + per-vault CMK. Separate PR.

5. **Cost-analysis follow-up actions** (per `docs/cost-analysis-2026-05-19.md`): VPC Endpoint audit (-$15-25/mo, the biggest single lever), ECS idle-scale-to-zero for 3-4 more services (-$3-6/mo), EC2-Other drill (-$2-4/mo), ELB LCU optimization (-$5-10/mo), CloudWatch custom-metric audit (-$2-4/mo), RDS Serverless v2 scale-to-zero (-$1-2/mo). Each is a focused agent dispatch.

6. **Disler fork patches** (per design Section 4.7): add `/health` endpoint (orchestrator confirmed it's missing 2026-05-19), add `Idempotency-Key` header processing (HIGH-05 recovery), drop unused `themes`/`theme_shares`/`theme_ratings` tables, add W3C trace_id/span_id/parent_span_id columns. Each is a small PR against `Aztec03hub/claude-code-hooks-multi-agent-observability`.

7. **dependency-updater agent**: scheduled run? Consider `CronCreate` for weekly autonomous sweep.

8. **PR-281 dead code cleanup**: the `coalesce(observability_kms_key_arn, aws_kms_key.api_gateway_logs.arn)` fallback chain in api-gateway-ws is now dead (the consolidated key always wins). Strip in a small chore PR; same for the `legacy `aws_kms_key.fallback_log[0]` retained-for-W2-T7-retirement pattern across multiple modules.

### Workflow tools shipped this session arc (recap)

| Tool | Purpose | Reference |
|---|---|---|
| `.claude/agents/dependency-updater.md` | Long-lived file-defined agent | PR #364, ADR-045 |
| `docs/templates/agent-brief.md` | Canonical inline-brief skeleton | PR #368 |
| `docs/templates/agent-brief-architect-reviewer.md` | Stage 1 of design-review cycle (Step 0 inventory mandatory) | PR #394, updated PR #395 |
| `docs/templates/agent-brief-adversarial-reviewer.md` | Stage 3 of design-review cycle | PR #394 |
| `scripts/design-review.sh` | Mechanical kickoff for the cycle | PR #395, branch-only-doc fix PR #401 |
| `scripts/verify-agent-run.sh` | 8-check trust-but-verify; merge-commit skip PR #401 | PR #371 + PR #401 |
| `scripts/pr-monitor.py` v6 | Live PR + CI state observability + ACTIONABLE-FAIL recipes | PR #385/#386/#397/#399 |
| `scripts/pr-unstick.sh` v3 | Close + reopen + preserve auto-merge + BEHIND nudge | PR #387/#396 |
| `scripts/branch-prune.sh` | Bulk-prune dangling local branches via PR state | PR #404 |
| `WORKFLOW.md` section 5.5 | PR monitor canonical procedure (v5+v6 docs) | PR #385 + PR #402 |
| `WORKFLOW.md` section 5.6 | Design Review Cycle | PR #394 |
| `.agent-runs/README.md` DONE spec | `status=success|failure` required | PR #396 |
| `.github/workflows/changelog-check.yml` `.agent-runs/*` exempt + label-triggers | Prevent stuck-PR pattern | PR #400 |
| ADRs 044-046 | Container Insights / file-defined agents / local-first verification | PR #370 |

### Active worktrees as of 2026-05-19 04:30 UTC

```
~/projects/panakoes                  [main]
~/projects/panakoes-telemetry-impl   [feat/telemetry-implementation] (agent in flight)
```

48 stale branches were bulk-pruned via scripts/branch-prune.sh; 5 merged worktrees pruned manually. Down from 49+ branches to clean state.

### Memory entries added 2026-05-19

- `feedback_architect_reviewer_must_search_existing_tools.md` (disler near-miss)
- `feedback_label_changes_dont_retrigger_workflows.md` (skip-changelog label stuck-PR)
- `feedback_terraform_apply_authority_granted.md` (Phil's tf-apply grant)
- `feedback_announce_auto_merge_disarm_intent.md` (Phil merged #409 not knowing it was held)
- `feedback_cherry_pick_lockfile_conflict_resolution.md` (already present, reinforced this arc)
- `workflow_branch_prune_cadence.md` (when to run branch-prune.sh)
- `reference_ecr_image_copy_recipe.md` (`docker buildx imagetools create` for v2 repo bootstrap)
- (Updated to RESOLVED: `feedback_pr_unstick_v3_needed_for_behind_after_unstick.md`)

---

## ORCHESTRATOR STATE (2026-05-19 01:10 UTC) - superseded by section above

**Read this section first; the older `2026-05-14` section below is historical context.**

### Session arc since 2026-05-18 marathon

The 2026-05-18 to 2026-05-19 marathon (project memory `panakoes-session-2026-05-18` has the full picture) accomplished: cost reduction (gross steady-state ~$153/mo to ~$80/mo, net $0 throughout via Activate Founders credits), full Dependabot queue clearance including Tailwind v4 + TS 6 + types/node 25 majors, and major workflow scaffolding (pr-monitor v4, pr-unstick v2, verify-agent-run, agent-brief template, dependency-updater agent, design-review cycle canonicalized). 24+ PRs merged.

The continuation session 2026-05-19 (this one) is a near-direct continuation: Gate 1 of the tool-trace telemetry design landed (PR #393), workflow improvements compounded (PR #395, PR #396), Stage 3 adversarial review is running as of the last update.

### In-flight as of 2026-05-19 01:10 UTC

| Item | State | Next |
|---|---|---|
| PR #393 telemetry design | OPEN, BLOCKED on auto-merge waiting for adversarial Stage 3 | Wait on adversarial-reviewer agent `a91e35267caafec81`; Gate 2; merge |
| PR #396 pr-unstick v3 + DONE spec | OPEN, BLOCKED (CI running after force-with-lease) | Auto-merge will fire on green |
| Adversarial-reviewer agent | RUNNING in `panakoes-adversarial-review-tool-trace-telemetry` | Notification on completion |
| pr-monitor (Monitor tool task `bpth0ypat`) | RUNNING in persistent mode | Keep until session end |

### Backlog (priority order, all picked up by next-session Claude)

1. **Wave 2 KMS W2-T2..T7** (-$13/mo when complete). See `ARCH-MIGRATION.md` section 2.2. T1 already shipped (PR #365). Remaining tasks are mechanical migrations of existing per-service KMS keys to the consolidated `app-data` / `logs` CMKs.

2. **Telemetry implementation** (after PR #393 design merges). 6-8h estimated (agent's revised estimate post-Gate-1). Components: trace-shim.sh (12 hook events), telemetry-flusher.py (async drain + gitleaks redaction + dual-write), SQLite schema with W3C trace + OTel GenAI fields, disler server fork-evaluation + setup script, bench-hook.sh + check-bench-budget.py. Phil's Gate-1.5 decisions: OTel-only naming (no terse aliases), single shim script for all 12 events, hard-fail FS-type check, W3C fields go in disler's payload blob.

3. **Cost report Phil asked for** (skeleton may exist if prior session got to it before context compaction): "WHY are we still at $96/mo gross, WHAT is costing us that, and what we can do to further GREATLY reduce price WITHOUT credits." Needs live Cost Explorer drill + recommendations doc.

4. **45 stale local branches** still around (started at 52, deleted 7 this session). Many are squash-merged so `git branch --merged` misses them. Need a per-PR-state check + bulk delete script.

5. **Recurring micro-fixes:**
   - `scripts/verify-agent-run.sh`: Check 3 false-positive on gitignored `.agent-runs/` files (saw it during Gate 1 verify). Strip `.agent-runs/` from REPORT_FILES before comparison.
   - `scripts/pr-monitor.py` v5: suppress NO-CHECKS re-emit pattern (task #23).
   - `scripts/design-review.sh`: handle the case where design doc lives only on a branch, not the current worktree (task #33; surfaced during first real use today).

6. **dependency-updater agent**: defined at `.claude/agents/dependency-updater.md` but only one run executed so far (CVE bump 2026-05-18, PR #367). Could fire it scheduled (weekly) via `/loop` or `CronCreate`.

7. **`/loop` automation candidates**: pr-monitor is the only persistent observability today. Could add: hourly cost-anomaly poll, daily Dependabot security advisory scan, nightly dependency-updater dry-run.

### Workflow tools shipped this session arc (recap; canonical references)

| Tool | Purpose | Reference |
|---|---|---|
| `.claude/agents/dependency-updater.md` | Long-lived file-defined agent | PR #364, ADR-045 |
| `docs/templates/agent-brief.md` | Canonical inline-brief skeleton | PR #368 |
| `docs/templates/agent-brief-architect-reviewer.md` | Stage 1 of design-review cycle (Step 0 inventory mandatory) | PR #394, updated PR #395 |
| `docs/templates/agent-brief-adversarial-reviewer.md` | Stage 3 of design-review cycle | PR #394 |
| `scripts/design-review.sh` | Mechanical kickoff for the cycle | PR #395 |
| `scripts/verify-agent-run.sh` | 8-check trust-but-verify | PR #371 |
| `scripts/pr-monitor.py` v4 | Live PR + CI state observability | PR #385/#386 |
| `scripts/pr-unstick.sh` v3 | Close + reopen + preserve auto-merge + BEHIND nudge | PR #387/#396 |
| `WORKFLOW.md` section 5.5 | PR monitor canonical procedure | PR #385 |
| `WORKFLOW.md` section 5.6 | Design Review Cycle | PR #394 |
| `.agent-runs/README.md` DONE spec | `status=success|failure` required | PR #396 |
| ADRs 044-046 | Container Insights / file-defined agents / local-first verification | PR #370 |

### Active worktrees as of 2026-05-19 01:10 UTC

```
~/projects/panakoes                                            [main]
~/projects/panakoes-architect-review                           [reviews/architect-of-telemetry-design] (Stage 1 done; needed for Stage 3 reference; prune after PR #393 merges)
~/projects/panakoes-design-update                              [docs/telemetry-design-update-v2] (PR #393 push branch; prune after #393 merges)
~/projects/panakoes-adversarial-review-tool-trace-telemetry    [reviews/adversarial-of-tool-trace-telemetry] (Stage 3 in flight)
```

5 worktrees were pruned earlier this session (tier1-cuts, deferred-majors, queue-clearance, adr-catchup, dependabot-triage), freeing ~9GB.

### Memory entries added this session arc (post-compaction)

- `feedback_architect_reviewer_must_search_existing_tools.md` (from disler near-miss)
- (Updated: `feedback_pr_unstick_v3_needed_for_behind_after_unstick.md` marked RESOLVED in PR #396)

---

## ORCHESTRATOR STATE (2026-05-14, Wave 1 in progress)

**Read this section first.** Current live state for the orchestrator.

### New documents to read at session start (in addition to the standard three-file load)

1. **`ARCH-MIGRATION.md`**: the authoritative architecture state + migration plan. Contains current infra state, Wave 0-3 task lists with review checkpoints, orchestrator context-management guide, and agent dispatch templates. Read it in full at session start.

2. **`docs/service-contracts.md`**: per-service boundary contracts.

### Wave 1 status (2026-05-14, current)

| Task | PR | Status | Notes |
|---|---|---|---|
| W1-T1: Cloud Map namespace | #355 | MERGED + APPLIED | `panakoes-dev.local`, ID `ns-fpf4yyzsvqcyy2ld` |
| W1-T2: Shared internal ALB | #356 | MERGED + APPLIED | ALB `panakoes-dev-alb` ACTIVE; 11 TGs; listener on port 80 |
| W1-T3: API GW rewire + ALB header routing | #358 | OPEN, CI running | replace-allowed label added; plan: 8 add, 4 change (api-gw) + 8 change, 3 destroy (alb) |
| W1-T4: ECS Service Connect + ALB wiring | TBD | Agent running | Background agent `ac7e11b548cb30c8b` in worktree `panakoes-w1-ecs-sc` |
| W1-T5: NLB removal | -- | NOT STARTED | Blocks on W1-T3 + W1-T4 applied + W1-T6 verified |
| W1-T6: Curl verification | -- | NOT STARTED | Orchestrator runs after W1-T3 + W1-T4 applied |
| W1-T7: Update service-contracts.md | -- | NOT STARTED | After W1-T6 |

**Critical design decision (W1-T3):** ALB uses header-based routing (`X-Panakoes-Service: <key>`) instead of path-based routing. Reason: API GW strips the `/v1/<service>/` prefix before forwarding, so path rules (`/v1/auth/*`) would never match the stripped paths. Header routing avoids service code changes.

### Active worktrees

```
~/projects/panakoes              [main]
~/projects/panakoes-w1-apigw     [feat/w1-t3-apigw-alb-rewire]  -- PR #358, prune after merge
~/projects/panakoes-w1-ecs-sc    [feat/w1-t4-ecs-service-connect]  -- W1-T4 agent running
```

**Worktrees to prune** (already merged): `panakoes-w1-svcdisc` (W1-T1), `panakoes-w1-alb` (W1-T2). Run:
```bash
cd ~/projects/panakoes
git worktree remove ../panakoes-w1-svcdisc --force
git branch -D feat/w1-t1-service-discovery-namespace
git worktree remove ../panakoes-w1-alb --force
git branch -D feat/w1-t2-shared-alb
```

### Recently merged PRs (this session)

| PR | Title |
|---|---|
| #354 | fix(health-aggregator): repair test drift from PR #344 cloudwatch refactor |
| #355 | feat(infra): provision Cloud Map namespace panakoes-dev.local (W1-T1) |
| #356 | feat(infra): provision shared internal ALB with 11 TGs (W1-T2) |
| #357 | fix(ci): change stripe placeholder to avoid trivy false positive |

### Immediate next actions (in priority order)

1. **Wait for W1-T4 agent** to complete and review its PR (ECS Service Connect + ALB wiring).

2. **Wait for PR #358 CI** to pass (replace-allowed label applied, CI re-running). Auto-merge when green.

3. **After PR #358 + W1-T4 PR both apply:** Run W1-T6 curl verification for all 8 public routes.

4. **After W1-T6 clean:** Dispatch W1-T5 agent (NLB removal from `infra/dev/ecs/*.tf`).

5. **Prune stale worktrees** listed above.

6. **DynamoDB provider-v6 deprecation (chore, Wave 2):** 7 tables use deprecated `hash_key`/`range_key`. Logged as W2-C1 in ARCH-MIGRATION.md. Not blocking.

4. **DynamoDB provider-v6 deprecation (chore, Wave 2):** 7 tables in `infra/dev/data/main.tf` use deprecated `hash_key`/`range_key`. Logged as W2-C1 in ARCH-MIGRATION.md. Not blocking.

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
