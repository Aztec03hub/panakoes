# PLANNING.md: Architectural Decisions and Rationale

This document is the running journal of architectural decisions for Panakoes. It combines a fast-lookup decision register with longer-form Architecture Decision Records (ADRs) for the consequential choices.

When a decision is superseded, mark it `SUPERSEDED BY ADR-XXX` and write the new entry with full context. Do not delete superseded records; they document the evolution and the reasoning at each fork.

---

## Decision Register

Quick-reference table. Full reasoning for each entry lives below in the ADRs section or in inline rationale.

| ID | Area | Decision | Date | Status |
|---|---|---|---|---|
| ADR-001 | Project name | Panakoes (constructed Greek "all-hearing") | 2026-05-07 | Locked |
| ADR-002 | License | MIT | 2026-05-07 | Locked |
| ADR-003 | AWS region | us-east-1 | 2026-05-07 | Locked |
| ADR-004 | IaC tool | Terraform with remote state in S3 + KMS + DynamoDB lock | 2026-05-07 | Locked |
| ADR-005 | Auth | Better-Auth (TS service, JWT-based, RBAC + step-up MFA) | 2026-05-07 | Locked |
| ADR-006 | Repo structure | Monorepo (services, infra, web, tests, docs) | 2026-05-07 | Locked |
| ADR-007 | Frontend stack | SvelteKit on S3 + CloudFront | 2026-05-07 | Locked |
| ADR-008 | Languages | Python primary across services, TypeScript for auth service | 2026-05-07 | Locked |
| ADR-009 | Transcription architecture | Dual-mode (async batch + live streaming) with pluggable Transcriber abstraction | 2026-05-07 | Locked |
| ADR-010 | GPU compute | g4dn.xlarge EC2 Spot for both modes | 2026-05-07 | Locked |
| ADR-011 | Streaming session model | Session-spawned GPU per active session (not always-on) | 2026-05-07 | Locked |
| ADR-012 | Whisper specifics | whisper-large-v3 at fp16; Silero VAD; Step Functions fan-out for >10 min | 2026-05-07 | Locked |
| ADR-013 | AI summarization | Claude Haiku 4.5 default; Sonnet 4.6 premium feature | 2026-05-07 | Locked |
| ADR-014 | Payments | Stripe (Free / Pro $12mo / Team $30/seat min 3) | 2026-05-07 | Locked |
| ADR-015 | Observability | CloudWatch + X-Ray + OpenTelemetry (ADOT) | 2026-05-07 | Locked |
| ADR-016 | Logging | CloudWatch Logs (30-day) + S3 archive + Athena | 2026-05-07 | Locked |
| ADR-017 | Audit trail | DynamoDB custom log + AWS CloudTrail | 2026-05-07 | Locked |
| ADR-018 | Testing | pytest + testcontainers (no DB mocks) + vitest + Playwright | 2026-05-07 | Locked |
| ADR-019 | MVP scope | v0.1 = async + streaming both included | 2026-05-07 | Locked |
| ADR-020 | Public-repo security | gitleaks pre-commit + GitHub secret scanning + OIDC federation + remote Terraform state + AWS Secrets Manager | 2026-05-07 | Locked |
| ADR-021 | Working modes for Claude Code | Orchestrator-delegation default, direct mode as exception | 2026-05-07 | Locked |
| ADR-022 | CHANGELOG and README discipline | Keep a Changelog format + update gate via GitHub Action | 2026-05-07 | Locked |

The decision register entries above (ADR-001 through ADR-022) predate the `docs/adr/` directory; full rationale lives inline in this file. New ADRs from ADR-021 (file form) onward are authored as standalone files under [`docs/adr/`](docs/adr/) and indexed in [`docs/adr/README.md`](docs/adr/README.md). Where a register row above shares a number with a file-form ADR (ADR-021, ADR-022), the file-form ADR is the canonical source; the register row is the fast-lookup pointer.

### File-form ADRs (docs/adr/)

| ID | Title | One-line summary |
|---|---|---|
| [ADR-021](docs/adr/ADR-021-worktree-convention-for-parallel-agents.md) | Worktree Convention for Parallel Sub-Agents | Concurrent sub-agents MUST run in dedicated git worktrees branched from `origin/main` to prevent shared-index corruption. |
| [ADR-022](docs/adr/ADR-022-jwt-hs256-then-rs256.md) | JWT Signing: HS256 in Slice 1, RS256 + JWKS in Slice 2 | Auth service signs HS256 with a shared secret in slice 1; migrates to RS256 + JWKS for production credibility in slice 2. |
| [ADR-023](docs/adr/ADR-023-audit-library-three-backends.md) | Audit Library with Three Backends | `panakoes-audit` ships Memory, Stdout, and DynamoDB backends behind one `AuditStore` Protocol, env-var-selected, gated at 100% coverage. |
| [ADR-024](docs/adr/ADR-024-orchestrator-delegation-pattern.md) | Orchestrator-Delegation as Default Working Mode | Top-level Claude decomposes work into focused briefs, spawns parallel sub-agents in worktrees, verifies output against the brief and the run report, integrates only verified work. |
| [ADR-025](docs/adr/ADR-025-agent-run-report-schema.md) | Agent Run Report Schema | Every agent invocation that touches files emits a structured report at `.agent-runs/<UTC-timestamp>-<slug>.md` with YAML frontmatter and a markdown body. |
| [ADR-026](docs/adr/ADR-026-changelog-merge-union.md) | CHANGELOG.md Merge=Union | `.gitattributes` declares `CHANGELOG.md merge=union` so concurrent appends to `[Unreleased]` stop producing conflicts. Scoped narrowly to CHANGELOG.md. |
| [ADR-027](docs/adr/ADR-027-ci-workflow-concurrency-cancel-in-progress.md) | CI workflow concurrency: cancel in progress | Per-PR concurrency groups cancel superseded CI runs to save minutes + cut noise. |
| [ADR-028](docs/adr/ADR-028-auto-update-prs-pat-authentication.md) | Auto-update PRs via PAT | `gh pr update-branch` workflow runs under a dedicated PAT (default `GITHUB_TOKEN` lacks the required scope). |
| [ADR-029](docs/adr/ADR-029-dependabot-grouped-minor-patch.md) | Dependabot grouped minor + patch | Group minor/patch bumps per ecosystem to one weekly PR; majors stay individual. |
| [ADR-030](docs/adr/ADR-030-ruleset-bypass-actor-for-emergencies.md) | Ruleset bypass actor for emergencies | Repo admin bypass-actor exists for documented emergencies only; routine work goes through PRs. |
| [ADR-031](docs/adr/ADR-031-cost-api-read-through-cache.md) | cost-api read-through cache | Cost API caches Cost Explorer responses in-process to stay under CE's per-call cost ($0.01). |
| [ADR-032](docs/adr/ADR-032-tier-3-lifecycle-safety-pattern.md) | Tier-3 lifecycle safety pattern | Admin Tier-3 destructive actions require typed confirmation + step-up MFA + audit log. |
| [ADR-033](docs/adr/ADR-033-tier-3-response-code-semantics.md) | Tier-3 response code semantics | Standard HTTP semantics for Tier-3 lifecycle endpoints (202 accepted, 409 conflict, 423 locked). |
| [ADR-034](docs/adr/ADR-034-cloudfront-standard-logs-v2.md) | CloudFront standard logs v2 | Use CloudFront standard logs v2 (Parquet to S3) over legacy v1 for cheaper Athena queries. |
| [ADR-035](docs/adr/ADR-035-new-aws-account-friction-mitigations.md) | New AWS account friction mitigations | Catalog of first-launch gotchas (PendingVerification, default concurrency 10, OCI manifest rejection). |
| [ADR-036](docs/adr/ADR-036-aurora-serverless-v2-scale-to-zero.md) | Aurora Serverless v2 scale-to-zero | Auth DB uses ASv2 with min ACU 0 to pay only for active connections. |
| [ADR-037](docs/adr/ADR-037-pluggable-transcriber-three-backends.md) | Pluggable transcriber: three backends | `panakoes-transcriber` ships Whisper (local GPU), Groq (hosted), and a Fake backend behind one Protocol. |
| [ADR-038](docs/adr/038-api-gateway-routing-strategy.md) | API Gateway routing: proxy default + explicit overrides | Per-service `ANY /v1/<svc>/{proxy+}` catch-all is the default; explicit overrides layer on top for per-route throttling, auth, or metrics. |
| [ADR-039](docs/adr/ADR-039-auth-db-application-role-and-migration-runner.md) | Auth DB role split + migration runner | Auth service runs as least-privileged `panakoes_auth_app` (DML only); migrations run via a separate operator-invoked task as the owner role (DDL). |
| [ADR-040](docs/adr/ADR-040-tenant-cost-rollup-service-dimension.md) | Tenant cost rollup: per-service dimension | Sort key becomes composite `day_service` (`YYYY-MM-DD#<service>`) so the per-tenant page can show per-service breakdown. |
| ADR-041 | RS256 + KMS + JWKS migration path | Plan to retire HS256 (ADR-022 slice 1) by signing JWTs with an AWS KMS asymmetric key and publishing a JWKS endpoint. Doc landed today; implementation pending. |
| ADR-042 | MFA step-up enforcement (deferred) | Step-up MFA design captured but enforcement deferred until billing endpoints land; ADR documents the deferral and the trigger that unblocks it. |
| [ADR-044](docs/adr/ADR-044-container-insights-cost-tradeoff.md) | Container Insights cost vs observability tradeoff in dev | Disable ECS Container Insights in dev to drop the $44/mo line item (~30% of post-Wave-1 dev bill); health-aggregator falls back to ECS DescribeServices / DescribeTasks for the basic view. Production re-enables once steady-state metric volume is known. |
| [ADR-045](docs/adr/ADR-045-file-defined-long-running-agents.md) | File-defined long-running agents under `.claude/agents/` | Recurring sub-agents are defined as Markdown files under `.claude/agents/`, versioned and PR-reviewed, dispatched by name. One-shot agents continue to use inline briefs from `docs/templates/agent-brief.md`. Both produce run reports in `.agent-runs/`. |
| [ADR-046](docs/adr/ADR-046-local-first-verification-discipline.md) | Local-first verification as orchestrator discipline | Every agent brief MUST include a "Local-First Verification" section naming the exact commands, mandating FULL output capture in the run report, and defining a stop condition on failure. Orchestrator verification is the second-line check; server-side CI is the third, not the first. |

---

## Architecture Decision Records (full)

Long-form context for the most consequential choices. Routine decisions stay in the register table above.

### ADR-009: Transcription Architecture (Dual-Mode Pluggable)

**Context.** The product needs to transcribe both uploaded audio files (where minutes of latency is acceptable) and live wearable audio streams (where seconds of latency is the bar). Whisper is current SOTA but the field moves quickly; a model swap should be a config change, not a rewrite.

**Decision.** Two transcription paths sharing a `TranscriberBackend` Python protocol:
- **Async batch path:** S3 upload event → Lambda enqueue → AWS Batch with EC2 g4dn.xlarge Spot GPU running whisper-large-v3 at fp16 → S3 transcript output. Pennies per audio hour. Cold start of ~2-5 minutes is acceptable for async.
- **Streaming path:** API Gateway WebSocket → Session Manager Lambda → Spawner Lambda → ECS-managed g4dn.xlarge Spot GPU spawned per session, running faster-whisper-large + Silero VAD → WebSocket → client. Sub-second latency once warm; ~45-80 seconds session warmup is hidden behind a "connecting..." UI state.

Both paths implement the same `TranscriberBackend` Protocol; future model swaps are config-driven, not rewrites.

**Consequences.**
- Adds AWS Batch, ECS, EC2 GPU, Step Functions, API Gateway WebSocket to the AWS service map (positive for portfolio).
- Custom GPU AMI required for fast streaming session warmup; one-time DevOps work.
- Cost is bounded: batch is essentially free at idle, streaming is $0.16 per session-hour.
- AWS Activate Founders credits ($1,000) cover ~10 years of expected dev streaming usage.

**Implementation status (night two, 2026-05-08 UTC).** The S3-event entry point shipped as the `event-router` Lambda (PR #48) and the upload-bucket Terraform (PR #23). The `transcriber-batch`, `transcriber-stream`, `session-manager`, `gpu-spawner`, and `summarization` microservices are not yet built; the AWS Batch compute environment, GPU AMI Packer scaffold, Step Functions long-audio fan-out, and API Gateway WebSocket Terraform are all open PRs (#54, #55, #58, #59) tracking the remaining work.

### ADR-011: Streaming Session Model (Session-Spawned, not Always-On)

**Context.** Live streaming demands either (a) always-on GPU (zero cold start, ongoing cost), or (b) session-spawned GPU (~45-80s warmup at session start, near-zero idle cost), or (c) third-party hosted streaming ASR (vendor lock, recurring cost).

**Decision.** Session-spawned GPU per active session.

**Why.** Phil's expected usage during dev and early product life is low enough that always-on is overkill ($115/mo idle cost) while session-spawned is cents per actual session. Session warmup is hidden behind a deliberate "connecting..." UI state, which feels intentional rather than broken. The pluggable Transcriber abstraction means we can promote any session to always-on later if traction justifies it.

**Consequences.**
- Need custom AMI baked with NVIDIA drivers, Docker, faster-whisper container layer, and Whisper-large weights to keep warmup time bounded.
- Need Session Manager + Spawner Lambdas to orchestrate lifecycle.
- Need idle-timeout enforcement to prevent runaway costs.

### ADR-015: Observability Stack (CloudWatch + X-Ray + OpenTelemetry)

**Context.** Need metrics, logs, traces, and dashboards. Options include CloudWatch-only, Grafana Cloud, Honeycomb, Datadog, New Relic, and various AWS-native combinations.

**Decision.** CloudWatch (metrics + dashboards + alarms) + AWS X-Ray (distributed tracing) as the backends. OpenTelemetry SDKs in every service emitting via the AWS Distro for OpenTelemetry (ADOT). Logs flow to CloudWatch Logs, then lifecycle to S3 after 30 days, then queryable via Athena.

**Why.**
1. AWS-portfolio defensibility. CloudWatch + X-Ray + Athena are three more named entries on the AWS depth checklist (relevant for the Scigon Senior Manager Solutions Architecture role).
2. Cost discipline. Free tier covers all dev usage. Steady-state cost at production scale is ~$5-15/mo, an order of magnitude under Datadog or paid Grafana tiers.
3. Vendor neutrality. OpenTelemetry instrumentation means tools below it are swappable. Sending a copy to Honeycomb or Grafana Cloud later is an exporter config change, not an app rewrite.

**Consequences.**
- Every microservice instruments with OTel SDK on startup.
- Lambda functions get the ADOT layer.
- ECS tasks get the ADOT sidecar.
- Trace context propagates across HTTP, EventBridge, SQS, WebSocket boundaries.

**Implementation status (night two, 2026-05-08 UTC).** Python-side instrumentation shipped as `services/otel-lib/` (`panakoes-otel`, PR #46) and the CloudWatch log groups + S3 archive Terraform shipped as `infra/dev/observability/` (PR #32). The TypeScript-side equivalent (`@panakoes/otel`) is in flight as PR #42. ADOT Lambda layer attachment, ADOT ECS sidecar attachment, X-Ray sampling configuration, and CloudWatch dashboards/alarms remain pending; see SCOPE.md observability items.

### ADR-019: MVP Scope (v0.1 includes streaming)

**Context.** One-week build using Claude Code orchestrator-delegation. Streaming is the riskiest component (custom AMI, session-spawn, WebSocket routing) but also the highest portfolio-value feature.

**Decision.** Option B: ambitious MVP. v0.1 includes both async batch pipeline AND live streaming with a browser-mic demo. Admin Dashboard Tiers 1+2+3. Full test pyramid. Tier 4 (real-time event stream) deferred to phase 2.

**Why.** A polished v0.1 with both modes is significantly better portfolio material than a tight async-only MVP. If streaming slips, the fallback is to compress frontend polish or e2e test depth, not delay the milestone. The risk is bounded.

**Consequences.**
- Aggressive sequencing required. Streaming AMI + Session Manager + Spawner + WebSocket protocol all land in week 1.
- Tier 4 dashboard explicitly out of scope.
- If streaming proves harder than estimated, frontend polish gets compressed first.

See [`SCOPE.md`](docs/dev/SCOPE.md) for the in/out lists.

### ADR-020: Public-Repo Security Plan

**Context.** The repo is public on Phil's personal GitHub. Anyone can read source, history, and PR discussions. A single committed secret is permanently compromised the moment it lands on a public branch.

**Decision.** Layered defense:
- Local: `gitleaks` pre-commit hook scans every commit.
- Server: GitHub secret scanning + push protection enabled.
- App: Secrets via env vars at runtime, sourced from AWS Secrets Manager / SSM Parameter Store. No hardcoded credentials anywhere.
- Infra: Terraform state remote in S3 with KMS encryption and DynamoDB locking. State files never in repo.
- CI: GitHub Actions to AWS via OIDC federation. No long-lived AWS access keys.
- Repo: Branch protection on `main` (required PRs, required checks, no force push). Dependabot, CodeQL, and SECURITY.md enabled.

**Why.** "No secrets in source" is necessary but insufficient. Each layer assumes the prior layer might fail. Defense in depth is the only correct posture for a public repo handling production credentials.

**Consequences.** All work routes through PRs even when self-merged. CI must pass. AWS access from CI is short-lived per-job. Any secret rotation is automatic via Secrets Manager.

### ADR-021: Working Modes (Orchestrator-Delegation Default)

**Context.** Phil uses Claude Code with custom orchestrator-delegation patterns ("Claude Comms"). Top-level Claude decomposes work and delegates to parallel sub-agents that do the implementation. Verification runs at the orchestrator level.

**Decision.** Default mode for Panakoes work is orchestrator-delegation. Direct mode (top-level Claude does the work) is the explicit exception, used when a task is too small or too tightly coupled to delegate.

Every Agent invocation MUST instruct the sub-agent to read CLAUDE.md first, include acceptance criteria inline, and reiterate the discipline rules. Every sub-agent return MUST be verified against the brief before integration.

**Why.** Delegation parallelizes work and protects the orchestrator's context window. The verification gate ensures sub-agents can't slip discipline (as already happened once: an agent claimed "no em-dashes" while leaving 2 in its output; verification caught it).

**Consequences.** All sub-agent prompts include the CLAUDE.md read step and the discipline reminder. Templates for common patterns live in CLAUDE.md (and will graduate to `.claude/agent-briefs/` once that pattern stabilizes).

---

## Notes on Future Evolution

This document expands as decisions are made. When a decision is revisited, append a new ADR rather than editing the original. The goal is for a future engineer (or future Phil, or a curious interviewer) to be able to read this file and understand not just what we built, but why we built it that way and what the alternatives were.

---

## Night-two implementation log

**Window.** 2026-05-07 ~23:30 CDT through 2026-05-08 ~01:00 CDT (about 90 minutes wall-clock; mostly autonomous orchestrator-delegation with parallel sub-agents in worktrees). Scope across the session: PRs #23 through #59.

**Services shipped (Python).** `services/audit-lib/` (`panakoes-audit`, PR #18), `services/ingestion-api/` (PR #25), `services/models-lib/` (`panakoes-models`, PR #37), `services/query-api/` (PR #43), `services/middleware-lib/` (`panakoes-middleware`, PR #45), `services/otel-lib/` (`panakoes-otel`, PR #46), `services/event-router/` (PR #48). The Auth service (TypeScript, PR #11) shipped in the lead-in window.

**Infra shipped (Terraform, all in `infra/dev/`).** `network/` (VPC + subnets + NAT, PR #10), `data/` (DynamoDB tables: ingestion, audit-log, streaming-sessions, PR #24), `storage/` (S3 buckets: audio-uploads, transcripts, log-archive, PR #23), `iam/` (least-privilege per-service task + execution roles for 11 services, PR #34), `secrets/` (AWS Secrets Manager + dedicated CMK, PR #29), `ecr/` (11 repos + shared CMK, PR #28), `observability/` (CloudWatch log groups + metric filters + S3 archive + IAM, PR #32), `waf/` (WAFv2 web ACL not yet associated, PR #50).

**Docs shipped.** `docs/adr/` directory created with ADR-021 through ADR-026 (worktree convention, JWT slice 1/2, audit three-backends, orchestrator-delegation pattern, agent-run-report schema, CHANGELOG merge=union) plus the index README (PR #31). `docs/runbooks/` added with disaster-recovery, incident-response, and dev-troubleshooting runbooks (PR #39). `docs/architecture.md` shipped via PR #53.

**CI / discipline shipped.** Em-dash detector + actionlint pre-commit hooks (PR #30), `.gitattributes` with `CHANGELOG.md merge=union` driver (PR #27), changelog-check exemptions for `CLAUDE.md` / `PLANNING.md` / `SCOPE.md` (PR #26), Dependabot CHANGELOG-skip fix (PR #14), Auth CI fixes for pnpm 11 + Node 24 (PRs #12, #13).

**Still pending (open PRs at session end).** TypeScript otel lib (`@panakoes/otel`, PR #42), auth-client lib (PR #36), test-helpers lib (PR #52), Notification API (PR #40), Session Manager (PR #47), Summarization API (PR #51), `infra/dev/events/` (EventBridge + SNS + SQS, PR #33), `infra/dev/vpc-endpoints/` (PR #44), `infra/dev/backup/` (PR #49), `infra/dev/batch/` (PR #55), `infra/dev/api-gateway/` (PR #58), `infra/dev/frontend/` (PR #57), `infra/dev/step-functions/` (PR #59), SvelteKit admin skeleton + Tier 1 dashboard (PR #56), repo-hardening workflows (scorecard, trivy, license-check, PR title lint, etc., PR #38), CLAUDE.md night-two-learnings update (PR #41). The GPU AMI Packer scaffold (PR #54) merged late in the session.

**Not yet started.** `transcriber-batch`, `transcriber-stream`, `gpu-spawner`, `billing` microservices. End-to-end Playwright suite. Public marketing landing page. CloudWatch dashboards + alarms wiring on top of the log-group module. ADOT Lambda layer + ECS sidecar attachment.

---

## ADR journal entries (chronological, append-only)

Entries below cover ADRs landed after the night-two window. The file-form ADR index table above is the canonical pointer; this section is the narrative reading order with cross-references to memory and PRs.

### 2026-05-08 through 2026-05-09 wave (ADR-027 through ADR-037)

Captured in the file-form ADR index above. Highlights: CI concurrency cancel-in-progress (ADR-027), auto-update PRs via PAT (ADR-028), Dependabot grouping (ADR-029), ruleset bypass actor (ADR-030), cost-api read-through cache (ADR-031), Tier-3 lifecycle safety pattern + response codes (ADR-032 / ADR-033), CloudFront standard logs v2 (ADR-034), new-AWS-account friction catalog (ADR-035), Aurora Serverless v2 scale-to-zero for auth-db (ADR-036), pluggable transcriber with three backends (ADR-037).

### 2026-05-10: ADR-038 API Gateway routing strategy (proxy default + explicit overrides)

PR #197 deferred the proxy-vs-explicit simplification with a full pros/cons matrix in memory (`panakoes_api_gateway_proxy_route_simplification_deferred.md`). Phil reviewed the matrix on 2026-05-10 and chose the (c+) shape: per-service `ANY /v1/<service>/{proxy+}` catch-all as the default, layered explicit overrides on top for per-route throttling, distinct authorizers, or per-route CloudWatch dimensions. Closes the "explicit-everywhere drift" failure mode (two-repo coupling per endpoint) without losing the per-route policy hooks. Resolves the open decision "api-gateway routing shape" carried since PR #197.

### 2026-05-11: ADR-039 Auth DB role split + operator-invoked migration runner

Auth service originally ran as the cluster's master role (full DDL + DML on the auth schema), and Better-Auth migrations had no defined invocation point. ADR-039 splits the credentials: the running ECS service uses `panakoes_auth_app` (INSERT / SELECT / UPDATE / DELETE on the four Better-Auth tables only), and the migration runner uses the owner role for DDL only. Migrations run as an operator-invoked one-shot ECS task, not at container startup, eliminating the rolling-deploy DDL race and decoupling schema rollouts from image rollouts. Resolves the open decision "auth-db credential scope" and "migration invocation timing."

### 2026-05-11: ADR-040 Tenant cost rollup service dimension

The `panakoes-dev-tenant-cost-rollup` DynamoDB table was keyed `(tenant_id HK, day RK)` with a single `cost_cents` attribute. Admin Tier-2 design (`docs/design/admin-dashboard-tier-2-3.md`) requires a per-service breakdown beneath each tenant row. ADR-040 changes the sort key to composite `day_service` (`YYYY-MM-DD#<service>`), keeps `tenant_id` as the partition key, and updates the cost-rollup-aggregator to call Cost Explorer with a two-dimensional `GroupBy = [{TAG: tenant_id}, {DIMENSION: SERVICE}]`. Table is empty in dev, so the first apply replaces it cleanly. Resolves the open decision "tenant cost rollup schema" flagged by PR #228's cost-seed-agent.

### 2026-05-11: ADR-041 RS256 + KMS + JWKS migration path

ADR-022 split JWT signing into slice 1 (HS256 with a shared secret) and slice 2 (RS256 + JWKS, deferred). ADR-041 fills in the migration path: sign with an AWS KMS asymmetric key (no private key material in the auth container), publish a JWKS endpoint at `/.well-known/jwks.json` served by the auth service from the KMS public key, and rotate by issuing both keys in the JWKS during a cutover window. Implementation is pending (no service code yet); the ADR pins the design so the work is unblocked when scheduled. Supersedes the slice-2 placeholder in ADR-022.

### 2026-05-11: ADR-042 MFA step-up enforcement (deferred)

Better-Auth supports step-up MFA. ADR-042 captures the design (TOTP factor enrollment, step-up challenge issuance, freshness window on the access token) and explicitly defers enforcement to the PR that introduces the first billing-mutation endpoint. Trigger that unblocks enforcement: billing service ships the `POST /v1/billing/subscription` route. Without that trigger, enforcement has no caller to gate, so the work is documented but not started. Resolves the open decision "when does step-up MFA become a hard requirement."

---

## Current state: services (as of 2026-05-11 wave 5)

Service inventory + per-service deploy state. The night-two implementation log above remains the source of truth for the original ship dates; this section captures the live state.

| Service | Type | Code state | Deploy state | Notes |
|---|---|---|---|---|
| `services/auth/` | TypeScript (Hono + Better-Auth) | Shipped | Deployed | Image tag `migrate-018532f`; running as `panakoes_auth_app` per ADR-039 |
| `services/cost-api/` | Python (FastAPI) | Shipped | Deployed | Per-service cost breakdown per ADR-040 |
| `services/admin-api/` | Python (FastAPI) | Shipped | Deployed | Aggregates CloudWatch + Cost Explorer + DDB for admin SPA |
| `services/ingestion-api/` | Python (FastAPI) | Shipped | TF defined, not yet applied | ECR repo + IAM role provisioned; ECS service not yet up |
| `services/query-api/` | Python (FastAPI) | Shipped | TF defined, not yet applied | Same posture as ingestion-api |
| `services/summarization/` | Python (Lambda) | In flight | Not deployed | PR #229 |
| `services/notification/` | Python (Lambda) | In flight | Not deployed | PR #229 |
| `services/session-manager/` | Python (Lambda) | In flight | Not deployed | PR #229 |
| `services/billing/` | Python (Lambda) | In flight | Not deployed | Skeleton PR #229; Stripe webhooks PR #247 |
| `services/health-aggregator/` | Python | In flight | In flight | Service PR #231; TF PR #267 |
| `services/cost-rollup-aggregator/` | Python (Lambda) | Shipped | Deployed | Two-dimensional GroupBy per ADR-040 |
| `services/event-router/` | Python (Lambda) | Shipped | Deployed | S3 → DDB state flip + EventBridge publish (task #75) |
| `services/gpu-spawner/` | Python (Lambda) | Shipped | TF in flight | PR #271 (TF module) |
| `services/transcribe-worker/` | Python (Batch GPU container) | Shipped | Deploy in flight | Image baked; AWS Batch job-def PR queued |
| `services/transcriber-lib/` | Python lib | Shipped | n/a | Pluggable backend Protocol per ADR-037 |
| `services/transcriber-groq/` | Python lib | Shipped | n/a | Hosted-ASR backend |
| `services/audit-lib/` | Python lib | Shipped | n/a | Three backends per ADR-023 |
| `services/middleware-lib/` | Python lib | Shipped | n/a | |
| `services/models-lib/` | Python lib | Shipped | n/a | Pydantic v2 contract types |
| `services/otel-lib/` | Python lib | Shipped | n/a | |
| `services/otel-lib-ts/` | TypeScript lib | Shipped | n/a | TS equivalent of `panakoes-otel` |
| `services/auth-client/` | Python lib | Shipped | n/a | Shared JWT validator |
| `services/test-helpers/` | Python lib | Shipped | n/a | |
| `services/admin/` | SvelteKit SPA | In flight | Not deployed | Tiers 1-3 wiring against admin-api |

---

## Current state: infrastructure inventory (as of 2026-05-11 wave 5)

| Module | State | Notes |
|---|---|---|
| `infra/bootstrap/` | Applied | Remote state (S3 + KMS + DynamoDB lock) |
| `infra/global/` | Applied | OIDC federation for GitHub Actions |
| `infra/dev/network/` | Applied | VPC + 3 AZ + NAT + flow logs |
| `infra/dev/data/` | Applied | DDB tables (ingestion, audit-log, streaming-sessions) |
| `infra/dev/storage/` | Applied | S3 buckets (audio-uploads, transcripts, log-archive) |
| `infra/dev/iam/` | Applied | Least-privilege roles for 11 services |
| `infra/dev/secrets/` | Applied | 7 placeholder secrets pending real-value writes (see `aws_secrets_panakoes_dev.md`) |
| `infra/dev/ecr/` | Applied | 11 immutable-tag repos |
| `infra/dev/observability/` | Applied | Log groups + S3 archive + metric filters |
| `infra/dev/waf/` | Applied | WebACL provisioned; first association via api-gateway |
| `infra/dev/events/` | Applied | EventBridge + SNS + SQS + DLQs |
| `infra/dev/vpc-endpoints/` | Applied | S3 gateway + Secrets/ECR/CloudWatch interface endpoints |
| `infra/dev/backup/` | Applied | AWS Backup plan |
| `infra/dev/security/` | Applied (PR #196 follow-up) | Per-service KMS + SG rules |
| `infra/dev/api-gateway/` | Partially applied | HTTP API + VPC link + KMS + log group up; integrations + WAF assoc deferred until first service-with-NLB lands. See `aws_api_gateway_partial_apply.md` |
| `infra/dev/auth-db/` | Applied | Aurora Serverless v2 (ADR-036); split-role per ADR-039 |
| `infra/dev/ecs/` | Applied | Cluster + capacity providers; first service (auth) running |
| `infra/dev/admin-state/` | Applied | Tenant cost rollup DDB table (composite sort key per ADR-040) |
| `infra/dev/cost-anomaly-monitor/` | Applied | CE anomaly monitor + DAILY EMAIL subscriber (per `aws_ce_anomaly_email_requires_daily.md`) |
| `infra/dev/cost-rollup-aggregator/` | Applied | Lambda + EventBridge cron + dedicated CMK log group |
| `infra/dev/transcribe-worker/` | Apply in flight | Lambda + dedicated CMK log group per `aws_lambda_log_group_dedicated_cmk_pattern.md` |
| `infra/dev/step-functions/` | TF defined, not yet applied | Long-audio fan-out state machine |
| `infra/dev/batch/` | TF defined, not yet applied | GPU compute env + job queue + job def |
| `infra/dev/frontend/` | TF defined, not yet applied | S3 + CloudFront + ACM + Route53 |
| `infra/packer/gpu/` | AMI baked | First Packer EC2 launch tripped PendingVerification (see `aws_pending_verification_first_ec2_launch.md`); resolved |

---

## Open decisions (current)

Decisions closed during this session are marked "Resolved" with the ADR or PR that closed them; they stay in the list as audit trail.

| Decision | Status | Pointer |
|---|---|---|
| api-gateway routing shape (proxy vs explicit-everywhere) | Resolved 2026-05-10 | ADR-038 (proxy + overrides) |
| auth-db credential scope (master vs least-privilege) | Resolved 2026-05-11 | ADR-039 (split-credential) |
| auth-db migration invocation timing (on-boot vs operator) | Resolved 2026-05-11 | ADR-039 (operator-invoked) |
| Tenant cost rollup schema (per-service breakdown) | Resolved 2026-05-11 | ADR-040 (composite sort key) |
| JWT signing slice 2 (HS256 → RS256) implementation path | Design pinned 2026-05-11 | ADR-041 (KMS + JWKS); implementation pending |
| When does step-up MFA become a hard requirement | Resolved 2026-05-11 | ADR-042 (deferred until billing mutation route) |
| api-gateway full integration set + WAF association | Open | Partial-apply state; triggers when first service-with-NLB lands |
| Glue catalog for CloudWatch log archive Athena queries | Open | Lifecycle to S3 shipped; Glue table pending |
| ADOT Lambda layer + ECS sidecar attachment | Open | OTel libs shipped; attachment pending |
| Always-on streaming GPU pool | Deferred to phase 3 | per SCOPE.md |
| Polyrepo split | Deferred | per ADR-006; revisit if monorepo CI time exceeds 20 min |

---

## Failure modes captured this session

Each lesson lands as a memory file in `~/.claude/projects/-mnt-c-Users-plafayette-Documents-Facebook/memory/`. Listed here so PLANNING.md is a self-contained index.

- **`feedback_panakoes_lessons.md`**: Parallel sub-agents need worktrees; polling foreground 10-30s not 5-15min background; Dependabot needs `--app dependabot` secrets; CHANGELOG-check must exempt Dependabot from day 0; required-check additions to branch-protection ruleset must happen IMMEDIATELY after first run; pnpm 10+ moved build-allowlist to `pnpm-workspace.yaml`; Terraform parallel-plan needs `-lock-timeout=2m`; nvm doesn't auto-load in non-interactive bash.
- **`feedback_pre_push_hook_must_finish_under_ssh_idle_timeout.md`**: `make ci-pr` over ~15 min trips SSH idle disconnect. Scope ci-pr to changed services, or hard 8-min budget.
- **`feedback_never_pipe_through_tail_in_background_bash.md`**: `tail -N` buffers until pipe close; captured output stays empty until the chain finishes.
- **`feedback_edit_tool_requires_read_first.md`**: Edit silently fails without prior Read; verify with `git status` / `git diff` before chaining.
- **`feedback_gitignore_build_artifacts_in_tree.md`**: Scripts writing to working tree need same-PR `.gitignore` coverage (or write under `dist/` / `tmp/`).
- **`feedback_verify_branch_before_commit.md`**: Auto-merge mid-edit can leave HEAD on main; verify branch immediately before every commit.
- **`feedback_sync_main_before_terraform_plan.md`**: When parallel agents merge Terraform PRs, local main is stale; `git pull --ff-only` before every `terraform plan`.
- **`feedback_terraform_plan_does_not_validate_aws_semantics.md`**: `terraform plan` does not catch AWS-side semantic rejections (CE subscriber/frequency pairings, managed-rule IDs, account quotas, KMS key-policy ARN patterns).
- **`aws_ecs_fargate_first_deploy_checklist.md`**: First ECS Fargate service hits a chain of 6+ failure modes: OCI manifest, secrets unreachable (VPC endpoints), KMS decrypt denied, S3 timeout for ECR layers (SG egress to S3 prefix list), single-arch image vs Graviton.
- **`aws_lambda_container_image_gotchas.md`**: Docker Buildx 29.x OCI manifest rejection (use `--provenance=false --sbom=false --output=type=image,oci-mediatypes=false,push=true`); default Lambda concurrency 10 not 1000; ECR tag-immutability blocks tag overwrites of failed pushes.
- **`aws_ce_anomaly_email_requires_daily.md`**: `aws_ce_anomaly_subscription` with EMAIL subscriber rejects `IMMEDIATE`; use DAILY or front with SNS.
- **`aws_lambda_log_group_dedicated_cmk_pattern.md`**: Shared logs CMK denies Lambda log groups (`/aws/lambda/*` outside the `EncryptionContext` condition); each Lambda module ships its own log CMK.
- **`aws_api_gateway_partial_apply.md`**: Partial-applied state for `infra/dev/api-gateway/`; leave as-is, revisit when first service-with-NLB lands.
- **`feedback_ci_local_first.md`**: Run `make ci-local` before every push; iterate until green locally. Goal is a pre-push hook.
- **`feedback_idle_time_is_failure.md`**: Don't poll CI after a designated list completes; switch hats to principal-engineer additions.

---

## Session journal

Per-session narrative logs live under the user's local memory directory. Read these first when picking up Panakoes work in a fresh session to recover full context without re-deriving.

| Session | Memory file | Window |
|---|---|---|
| Night-two | `panakoes_session_2026-05-08.md` | 2026-05-07 23:30 CDT to 2026-05-08 01:00 CDT |
| 2026-05-11 wave 5 | `panakoes_session_2026-05-12.md` (stub; created on next session start) | Multi-day session spanning 2026-05-09 to 2026-05-11; covers ADR-038 through ADR-042, auth deploy, cost-api per-service breakdown, api-gateway (c+) routing |
