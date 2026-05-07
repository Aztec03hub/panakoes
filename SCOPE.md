# SCOPE.md: MVP Scope and Phase-2 Backlog

This document defines what's in the v0.1 MVP and what's explicitly deferred. The goal is to ship a polished, complete v0.1 within roughly one week of focused build using Claude Code orchestrator-delegation.

If a feature is not on the In-Scope list, it does not ship in v0.1, no matter how tempting.

---

## v0.1 MVP: In Scope

### Core pipelines

- [ ] **Async batch transcription pipeline.** S3 upload → Lambda enqueue → AWS Batch + Spot GPU + Whisper-large-v3 fp16 → S3 transcript output → DynamoDB job tracking → EventBridge ready event → Lambda summarizer (Claude Haiku) → RDS metadata write → notification.
- [ ] **Live streaming transcription pipeline.** API Gateway WebSocket → Session Manager Lambda → Spawner Lambda → ECS-managed g4dn.xlarge Spot from custom AMI → faster-whisper-large + Silero VAD → WebSocket partial transcripts → idle timeout → graceful teardown.
- [ ] **AI summarization service.** Claude Haiku for default summaries; Sonnet selectively invoked when paid-tier "deep summary" is requested.

### Application services

- [ ] **Auth service** (TypeScript, Better-Auth on ECS Fargate). JWT issuing. RBAC with `user` and `admin` roles. Step-up MFA for sensitive actions.
- [ ] **Ingestion API** (Python, Lambda or ECS). Pre-signed S3 URL generation, audio file validation, ingestion intent recording.
- [ ] **Query API** (Python, ECS Fargate). List transcripts, get transcript by ID, search by metadata.
- [ ] **Billing service** (Python, Lambda). Stripe checkout session creation, webhook handler with idempotency, subscription state sync to RDS.
- [ ] **Notification service** (Python, Lambda). Email or webhook on transcript ready / billing event / error.
- [ ] **Audit trail** (Python, in-process module + DynamoDB writes). Application-level event recording with consistent schema.
- [ ] **Admin API** (Python, ECS Fargate). Aggregates CloudWatch + ECS + EC2 + Stripe + DynamoDB for the admin dashboard.

### Frontend

- [ ] **SvelteKit web app** on S3 + CloudFront.
- [ ] Public marketing landing page.
- [ ] Authenticated views: signup/login, upload, transcript list, transcript detail with summary, billing/settings.
- [ ] **Admin Dashboard** at `/admin` (role-gated):
  - Tier 1: Read-only health (service tiles, active counts, errors ticker, Stripe state).
  - Tier 2: Cost and budget tracker (AWS spend MTD, Claude API spend, projected month-end).
  - Tier 3: Lifecycle controls (restart, scale, kill stuck session, drain Batch, kill-switch, replay) gated by step-up MFA + audit logging.
- [ ] Live streaming demo: browser microphone capture, WebSocket connection, live transcript display.

### Infrastructure

- [ ] Terraform modules for: VPC, IAM roles, ECS cluster, Fargate services, Lambda functions, AWS Batch GPU compute environment, RDS Postgres, DynamoDB tables, S3 buckets, CloudFront distribution, API Gateway (REST + WebSocket), Secrets Manager, KMS keys, CloudWatch log groups + dashboards + alarms, X-Ray, EventBridge rules.
- [ ] Custom GPU AMI baked with NVIDIA drivers + Docker + faster-whisper image + Whisper-large weights (Packer build).
- [ ] Terraform remote state (S3 + KMS encryption + DynamoDB lock) bootstrapped via separate config.

### Observability and security

- [ ] OpenTelemetry instrumentation in every service via ADOT.
- [ ] CloudWatch metrics, logs, alarms.
- [ ] X-Ray distributed tracing.
- [ ] CloudWatch Logs lifecycle to S3 + Glue catalog for Athena queries.
- [ ] CloudTrail enabled for management events.
- [ ] DynamoDB audit log table.
- [ ] Pre-commit gitleaks hook installed and required.
- [ ] GitHub repo: secret scanning + push protection + branch protection + Dependabot + CodeQL.
- [ ] GitHub Actions to AWS via OIDC federation.
- [ ] AWS Secrets Manager and SSM Parameter Store for runtime secrets.
- [ ] Least-privilege IAM policies per service.

### Testing

- [ ] Unit tests across all Python services (pytest + pytest-asyncio).
- [ ] Integration tests against real Postgres / Redis via testcontainers (no DB mocks).
- [ ] AWS service integration tests via moto or LocalStack where appropriate.
- [ ] End-to-end Playwright tests covering: signup → upload → transcribe → summarize → bill → audit log visible.
- [ ] Coverage gates: 80% on application services, 100% on auth/billing/audit, 70% on infrastructure-adjacent code. CI fails PR below thresholds.

### CI/CD

- [ ] GitHub Actions on PRs: tests + lint + gitleaks + CodeQL + Terraform plan.
- [ ] GitHub Actions on merge to main: deploy to dev environment.
- [ ] GitHub Actions on tag push: cut release with auto-generated notes.
- [ ] CHANGELOG-updated check on PRs (skippable for `docs:` / `chore:` PRs via label).

### Documentation

- [x] LICENSE (MIT)
- [x] README.md
- [x] CHANGELOG.md
- [x] CLAUDE.md
- [x] PLANNING.md
- [x] SCOPE.md (this file)
- [ ] SECURITY.md
- [ ] CONTRIBUTING.md
- [ ] docs/architecture.md (with system diagram)
- [ ] Per-service READMEs

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
