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

See [`SCOPE.md`](SCOPE.md) for the in/out lists.

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
