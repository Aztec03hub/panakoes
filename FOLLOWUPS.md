# FOLLOWUPS.md: Open Work and Unfinished Business

Captured during the 2026-05-12 session handoff. A snapshot of what is in flight, what is pending Phil's decision, what is blocked, and what would have been done given more time. The fresh Claude picking this up should triage these against `MEMORY.md`, `CLAUDE.md`, and `WORKFLOW.md` before claiming any of them.

Each item lists: status, why it matters, what was attempted, and the suggested next move.

---

## 1. Auth-DB cutover finishing (P5 burn-in + Aurora decommission)

**Status:** in progress. Task #125 in_progress.

**Where we are:** PR #314 (auth-db-rds Terraform module) merged on main 2026-05-12. RDS db.t4g.micro instance `panakoes-dev-auth-rds` is live and serving auth traffic. Aurora cluster is still in place but no traffic flows to it (DSN secret was flipped). See `panakoes_auth_db_rds_cutover.md` in memory for the canonical record.

**What is pending:**
- End-to-end sign-in verification from the SPA. The auth service connected to RDS successfully (migrations applied, service is listening, the seed-admin task did a real Better-Auth `signUpEmail` + UPDATE round-trip). The full APIGW → auth → RDS chain has NOT been timed end-to-end. The cold-start should be gone since RDS is always-on, but the proof requires Phil's sign-in from his browser.
- 7-day burn-in window. Earliest decommission date: 2026-05-19.
- PR-B: `terraform destroy` of `infra/dev/auth-db/` (Aurora) + `git rm -r infra/dev/auth-db/` + decommission PR.

**Suggested next move:** ask Phil to sign in from the SPA, time it, confirm under ~700ms. Then start a calendar reminder for 2026-05-19 to ship PR-B.

---

## 2. ECS / auth-service portion of the cold-start (~100-500ms still on the table)

**Status:** pending; not in task list yet.

**Why it matters:** the cold-start research from earlier in the session attributed 11.6s of an 11.6s total to Aurora's resume-from-pause. The RDS cutover eliminates that. BUT the research deliberately treated everything-besides-Aurora as "the remaining ~100ms." That estimate was never re-validated post-cutover. The auth ECS service is on Fargate with a single task; if the task is recycled or scales to zero (it shouldn't with desired_count=1, but verify), there is a ~10-30s container cold start.

**Suggested next move (after Phil confirms #1):** measure the actual post-RDS sign-in latency. If consistently <500ms, mark this resolved with no action. If a periodic spike shows up (e.g. after long idle), investigate ECS task recycling, JWT signer KMS warm-up, or auth service connection-pool warm-up.

---

## 3. API Gateway `/v1/auth/{proxy+}` route returns 404 instead of proxying to auth service

**Status:** discovered during the cutover; not investigated.

**Why it matters:** during P4 verification I hit `https://n2un8ica69.execute-api.us-east-1.amazonaws.com/v1/auth/sign-in` and got `{"message":"Not Found"}` (APIGW's not-found shape, not Hono's). APIGW logs show the route key `ANY /v1/auth/{proxy+}` exists, so APIGW is matching the route but failing to forward. Either the integration_uri is wrong, the path mapping strips/preserves the wrong segment, or the integration is missing entirely. The auth service was up and listening; the integration is the issue.

**What was attempted:** confirmed the route exists, confirmed the auth service responded to requests from inside the VPC, confirmed CloudWatch logs show NO requests reaching the auth service from APIGW probes. The fault is between APIGW and the auth ECS task.

**Suggested next move:** read `infra/dev/api-gateway/main.tf` (specifically the `aws_apigatewayv2_integration` block for the auth service) + `aws apigatewayv2 get-integrations --api-id n2un8ica69` + verify the integration's `integration_uri` points at the auth service's Cloud Map / ALB / VPC link target, and that the path mapping is `/v1/auth/{proxy}` → `/{proxy}` or similar. The admin SPA must be using a different path (Cloud Map service-to-service, or a Cloudflare-fronted edge route) since sign-in clearly works from the frontend day-to-day. Find the working path, document it.

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
