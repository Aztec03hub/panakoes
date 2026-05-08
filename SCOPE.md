# SCOPE.md: MVP Scope and Phase-2 Backlog

This document defines what's in the v0.1 MVP and what's explicitly deferred. The goal is to ship a polished, complete v0.1 within roughly one week of focused build using Claude Code orchestrator-delegation.

If a feature is not on the In-Scope list, it does not ship in v0.1, no matter how tempting.

**Status legend.** `✓ shipped` = merged to `main`; `⏳ in progress` = open PR or actively under construction; `▢ pending` = on the v0.1 list but not yet started; `⊘ deferred to phase 2` = was on the original v0.1 list but has been intentionally moved out.

---

## v0.1 MVP: In Scope

### Core pipelines

- ▢ **Async batch transcription pipeline.** S3 upload → Lambda enqueue → AWS Batch + Spot GPU + Whisper-large-v3 fp16 → S3 transcript output → DynamoDB job tracking → EventBridge ready event → Lambda summarizer (Claude Haiku) → RDS metadata write → notification. (Entry-point pieces shipped: `event-router` Lambda PR #48 flips DDB state and emits `panakoes.ingest / AudioUploaded`; the audio-uploads / transcripts S3 buckets shipped via PR #23. The Batch compute environment, GPU AMI, transcriber-batch container, and summarizer remain.)
- ▢ **Live streaming transcription pipeline.** API Gateway WebSocket → Session Manager Lambda → Spawner Lambda → ECS-managed g4dn.xlarge Spot from custom AMI → faster-whisper-large + Silero VAD → WebSocket partial transcripts → idle timeout → graceful teardown. (Streaming-sessions DDB table shipped via PR #24; Session Manager service is open as PR #47; API Gateway WebSocket Terraform is open as PR #58.)
- ⏳ **AI summarization service.** Claude Haiku for default summaries; Sonnet selectively invoked when paid-tier "deep summary" is requested. (Open PR #51.)

### Application services

- ✓ **Auth service** (TypeScript, Better-Auth on ECS Fargate). JWT issuing. RBAC with `user` and `admin` roles. Step-up MFA for sensitive actions. (Shipped PR #11. HS256 in slice 1 per ADR-022; RS256 + JWKS deferred to slice 2.)
- ✓ **Ingestion API** (Python, Lambda or ECS). Pre-signed S3 URL generation, audio file validation, ingestion intent recording. (Shipped PR #25.)
- ✓ **Query API** (Python, ECS Fargate). List transcripts, get transcript by ID, search by metadata. (Shipped PR #43.)
- ▢ **Billing service** (Python, Lambda). Stripe checkout session creation, webhook handler with idempotency, subscription state sync to RDS.
- ⏳ **Notification service** (Python, Lambda). Email or webhook on transcript ready / billing event / error. (Open PR #40.)
- ✓ **Audit trail** (Python, in-process module + DynamoDB writes). Application-level event recording with consistent schema. (Shipped as `services/audit-lib/` PR #18; three-backend architecture per ADR-023.)
- ▢ **Admin API** (Python, ECS Fargate). Aggregates CloudWatch + ECS + EC2 + Stripe + DynamoDB for the admin dashboard.
- ⏳ **Event Router Lambda** (Python). S3 ObjectCreated handler that flips ingestion state and publishes `panakoes.ingest / AudioUploaded`. (Shipped PR #48; was implicit in the original async pipeline scope, now broken out as its own service.)
- ⏳ **Session Manager** (Python). Streaming session lifecycle owner. (Open PR #47.)

### Shared libraries (Python and TypeScript)

This category emerged during night-two implementation; libraries were factored out as the same code patterns repeated across services. All Python libs ship a PEP 561 `py.typed` marker.

- ✓ **`panakoes-models`** (Python, `services/models-lib/`). Pydantic v2 cross-service contract types for users, ingestion, transcripts, summaries, sessions, notifications, billing, errors. 100% test coverage. (Shipped PR #37.)
- ✓ **`panakoes-audit`** (Python, `services/audit-lib/`). AuditEvent model + three backends (Memory / Stdout / DynamoDB) per ADR-023. (Shipped PR #18.)
- ✓ **`panakoes-otel`** (Python, `services/otel-lib/`). OpenTelemetry SDK setup + per-library auto-instrumentation hooks (FastAPI, boto3, httpx). (Shipped PR #46.)
- ✓ **`panakoes-middleware`** (Python, `services/middleware-lib/`). Reusable FastAPI middleware (rate limit, CORS, max body size, required headers, correlation id) and `@audit_event` decorator. (Shipped PR #45.)
- ⏳ **`@panakoes/otel`** (TypeScript). TypeScript-side equivalent of `panakoes-otel` for the Auth service and any future TS HTTP service. (Open PR #42.)
- ⏳ **`panakoes-auth-client`** (Python). Shared JWT-validation client every Python service uses to verify Auth-issued tokens. (Open PR #36.)
- ⏳ **`panakoes-test-helpers`** (Python). Shared test utilities (moto factories, JWT minting helpers, audit-store fakes, FastAPI TestClient builders). (Open PR #52.)

### Frontend

- ▢ **SvelteKit web app** on S3 + CloudFront.
- ▢ Public marketing landing page.
- ▢ Authenticated views: signup/login, upload, transcript list, transcript detail with summary, billing/settings.
- ⏳ **Admin Dashboard** at `/admin` (role-gated):
  - ⏳ Tier 1: Read-only health (service tiles, active counts, errors ticker, Stripe state). (Open PR #56 ships the SvelteKit skeleton + Tier 1.)
  - ▢ Tier 2: Cost and budget tracker (AWS spend MTD, Claude API spend, projected month-end).
  - ▢ Tier 3: Lifecycle controls (restart, scale, kill stuck session, drain Batch, kill-switch, replay) gated by step-up MFA + audit logging.
- ▢ Live streaming demo: browser microphone capture, WebSocket connection, live transcript display.

### Infrastructure (Terraform)

The original SCOPE listed a single bullet covering all Terraform modules. Night-two work split that into per-domain modules under `infra/dev/<domain>/` so each can be applied, reviewed, and rolled back independently.

- ✓ **`infra/bootstrap/`** Remote-state backend (S3 + KMS + DynamoDB lock). (Shipped pre-night-two.)
- ✓ **`infra/global/`** GitHub Actions OIDC federation. (Shipped pre-night-two.)
- ✓ **`infra/dev/network/`** VPC, 3 AZs, public + private subnets, single NAT (cost-disciplined for dev), VPC Flow Logs. (Shipped PR #10.)
- ✓ **`infra/dev/data/`** Three DynamoDB tables (ingestion, audit-log, streaming-sessions), all PAY_PER_REQUEST + SSE + PITR + deletion protection. (Shipped PR #24.)
- ✓ **`infra/dev/storage/`** Three S3 buckets (audio-uploads, transcripts, log-archive) with per-bucket CMK, versioning, public-access blocked, TLS-only policy. (Shipped PR #23.)
- ✓ **`infra/dev/iam/`** Least-privilege task + execution roles for 11 services. (Shipped PR #34.)
- ✓ **`infra/dev/secrets/`** AWS Secrets Manager + dedicated CMK + lifecycle ignore_changes. (Shipped PR #29.)
- ✓ **`infra/dev/ecr/`** 11 immutable-tag, scan-on-push ECR repos + shared CMK + lifecycle policy. (Shipped PR #28.)
- ✓ **`infra/dev/observability/`** CloudWatch log groups + metric filters + S3 archive + log-archiver IAM. (Shipped PR #32.)
- ✓ **`infra/dev/waf/`** Regional WAFv2 web ACL with managed rule groups + rate limit + redacted logging. (Shipped PR #50.) Web-ACL associations pending.
- ⏳ **`infra/dev/events/`** EventBridge custom bus + SNS topics + SQS queues + DLQs. (Open PR #33.)
- ⏳ **`infra/dev/vpc-endpoints/`** Gateway and interface VPC endpoints to keep traffic on AWS backbone. (Open PR #44.)
- ⏳ **`infra/dev/backup/`** AWS Backup plan covering DDB + RDS. (Open PR #49.)
- ⏳ **`infra/dev/batch/`** AWS Batch GPU compute environment + job queues + job definitions. (Open PR #55.)
- ⏳ **`infra/dev/api-gateway/`** HTTP API + WebSocket API + custom domain. (Open PR #58.)
- ⏳ **`infra/dev/frontend/`** S3 origin bucket + CloudFront distribution + ACM cert + Route53. (Open PR #57.)
- ⏳ **`infra/dev/step-functions/`** Long-audio fan-out state machine for files > 10 minutes. (Open PR #59.)
- ✓ **GPU AMI Packer scaffold** at `infra/packer/gpu/`. NVIDIA drivers + Docker + faster-whisper image + Whisper-large weights. (Shipped PR #54.)
- ▢ **`infra/dev/security/`** Per-service KMS key bundling (or merged into existing per-domain keys). (Item from original scope; revisit once VPC endpoints + frontend land to confirm we still want a separate module.)

### Observability and security

- ⏳ **OpenTelemetry instrumentation in every service via ADOT.** Python lib shipped (PR #46); TS lib in flight (PR #42). ADOT Lambda layer attachment + ADOT ECS sidecar attachment + collector pipeline pending.
- ⏳ **CloudWatch metrics, logs, alarms.** Log groups + metric filters + archive shipped via `infra/dev/observability/` PR #32. Dashboards and alarms pending.
- ▢ **X-Ray distributed tracing.** Endpoint-emit ready in `panakoes-otel`; X-Ray exporter wiring pending.
- ✓ **CloudWatch Logs lifecycle to S3 + Glue catalog for Athena queries.** (Lifecycle shipped via PR #32; Glue catalog pending.)
- ▢ CloudTrail enabled for management events.
- ✓ **DynamoDB audit log table.** (Shipped PR #24.)
- ✓ **Pre-commit gitleaks hook installed and required.** (Shipped pre-night-two; em-dash detector + actionlint added PR #30.)
- ✓ **GitHub repo: secret scanning + push protection + branch protection + Dependabot + CodeQL.** (Shipped pre-night-two.)
- ✓ **GitHub Actions to AWS via OIDC federation.** (Shipped pre-night-two.)
- ✓ **AWS Secrets Manager and SSM Parameter Store for runtime secrets.** (Secrets Manager + dedicated CMK shipped PR #29.)
- ✓ **Least-privilege IAM policies per service.** (Shipped PR #34.)
- ⏳ **Repo-hardening CI workflows** (OpenSSF Scorecard, Trivy, license-check, PR-title-lint). Open PR #38.

### Testing

- ⏳ Unit tests across all Python services (pytest + pytest-asyncio). (Coverage gates in place since PR #9; per-service tests landing as each service ships.)
- ⏳ Integration tests against real Postgres / Redis via testcontainers (no DB mocks). (Auth service uses testcontainers Postgres; pattern to be replicated as data-touching services land.)
- ⏳ AWS service integration tests via moto or LocalStack where appropriate. (moto in use across `panakoes-audit`, `ingestion-api`, `event-router`.)
- ▢ End-to-end Playwright tests covering: signup → upload → transcribe → summarize → bill → audit log visible.
- ⏳ Coverage gates: 80% on application services, 100% on auth/billing/audit, 70% on infrastructure-adjacent code. CI fails PR below thresholds. (Gates configured per service; the full matrix will be observable once all services have shipped.)

### CI/CD

- ✓ **GitHub Actions on PRs: tests + lint + gitleaks + CodeQL + Terraform plan.** (Pytest, vitest, gitleaks, CodeQL, changelog-check all live.)
- ▢ GitHub Actions on merge to main: deploy to dev environment.
- ✓ **GitHub Actions on tag push: cut release with auto-generated notes.**
- ✓ **CHANGELOG-updated check on PRs (skippable for `docs:` / `chore:` PRs via label).** (Shipped pre-night-two; Dependabot exemption fix in PR #14; `CLAUDE.md` / `PLANNING.md` / `SCOPE.md` exemptions added in PR #26.)

### Documentation

- ✓ LICENSE (MIT)
- ✓ README.md
- ✓ CHANGELOG.md
- ✓ CLAUDE.md
- ✓ PLANNING.md
- ✓ SCOPE.md (this file)
- ✓ SECURITY.md
- ✓ CONTRIBUTING.md
- ✓ docs/architecture.md (with system diagram). (Shipped PR #53.)
- ✓ docs/adr/ directory (ADR-021 through ADR-026 + index, PR #31)
- ✓ docs/runbooks/ (disaster recovery, incident response, dev troubleshooting; PR #39)
- ⏳ Per-service READMEs. (Every shipped service has a README; pattern continues per-service.)

---

## v0.1 MVP: Out of Scope (Deferred)

These are explicitly NOT in v0.1. They are good ideas; they just don't fit the week-one window.

### Phase 2 (next 1-2 weeks after MVP)

- Admin Dashboard Tier 4 (real-time event stream via WebSocket fanout from EventBridge or DynamoDB streams).
- Multi-org / team account features beyond the basic 3-seat-minimum Team tier.
- Webhook integrations for end users (Slack, Discord, Notion).
- Mobile companion app.
- Advanced search (full-text or semantic) over transcripts.
- Multi-language transcription (whisper-large-v3 supports it but the UI/UX needs design work).
- Custom vocabulary / domain-specific transcription tuning.
- Speaker diarization.
- RS256 + JWKS for Auth (per ADR-022 slice 2).

### Phase 3 (when product validates)

- Wearable BLE integration (depends on hardware iteration).
- HA / multi-AZ for streaming GPU.
- Always-on streaming GPU pool for premium tier (when concurrency justifies).
- Self-hosted streaming on Kubernetes (for enterprise customers who want their own deployment).

### Explicitly never

- Marketing-page A/B testing (this is engineering, not growth-hacking, until traction warrants).
- Crypto/blockchain anything.
- Non-MIT licensing or dual-licensing tricks.

---

## Risk Register

Items that could compress the v0.1 ship date.

| Risk | Likelihood | Mitigation |
|---|---|---|
| Custom GPU AMI build takes longer than expected | Medium | Start AMI build day 1; have AWS Batch path with vanilla AMI as fallback if streaming slips |
| WebSocket routing from API Gateway to spawned EC2 has unexpected wiring complexity | Medium | Validate the routing pattern with a smoke test before building Session Manager logic |
| Whisper streaming latency on T4 is worse than expected | Low | Falls back to publishing batch-mode-only with streaming as v0.2 |
| AWS Activate credits don't land before steady-state cost matters | Low | Personal credit card covers gap; budget alerts at $50/$75/$90 keep ceiling visible |
| Stripe webhook idempotency edge cases in tests | Medium | Heavy testcontainers + replay test coverage; treat billing as the highest-coverage code path |
| Frontend polish gets compressed if streaming runs late | Medium-High | Acceptable; frontend can land at "functional, not pretty" for v0.1 with polish in v0.1.1 |

---

## Definition of Done for v0.1

v0.1 ships when:

1. A new visitor can sign up, upload an audio file, see the transcript and summary, upgrade to Pro, and use the live streaming demo end to end.
2. All tests pass in CI; coverage thresholds met.
3. Admin Dashboard Tiers 1+2+3 are functional and gated correctly.
4. CHANGELOG.md `[Unreleased]` section is moved to a `[0.1.0] - <date>` section, tagged `v0.1.0`, and a GitHub Release is published with notes.
5. The deployed dev environment is operational and the public URL works.
6. README.md reflects the actual shipped feature set.
7. No `[TBD]` placeholders remain in user-facing copy.
