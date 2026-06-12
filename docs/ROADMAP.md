# Roadmap

Panakoes is pre-alpha, actively developed. This document summarizes what is built, what is in flight, and what comes next.

For the detailed MVP scope decision log and phase-2 backlog, see [`docs/dev/SCOPE.md`](dev/SCOPE.md).

---

## What is built (shipped to `main`)

**Infrastructure**
- Remote-state backend (S3 + KMS + DynamoDB lock)
- GitHub Actions OIDC federation (zero long-lived AWS keys)
- VPC with 3 AZs, public + private subnets, VPC Flow Logs
- DynamoDB tables: ingestion jobs, audit log, streaming sessions
- S3 buckets: audio uploads, transcripts, log archive -- per-bucket CMK encryption
- IAM: least-privilege task + execution roles for all 11 services
- Secrets Manager + dedicated CMK
- ECR repos (11, immutable tags, scan-on-push)
- CloudWatch log groups + metric filters + S3 archive pipeline
- WAFv2 regional web ACL (managed rule groups + rate limiting)
- GPU AMI scaffold (NVIDIA drivers + Docker + faster-whisper + Whisper-large weights)
- 6 additional Terraform modules (KMS, ECS cluster, ALB, RDS, api-gateway, batch)

**Application services**
- Auth service (TypeScript, Better-Auth on ECS Fargate): JWT issuing, RBAC, step-up MFA
- Ingestion API (Python, Lambda): pre-signed S3 URL generation, audio validation
- Query API (Python, ECS Fargate): list/get/search transcripts
- Event Router Lambda: S3-trigger to EventBridge pipeline entry point
- Audit library: `panakoes-audit` with DynamoDB, stdout, and in-memory backends
- Models library: `panakoes-models`, Pydantic v2 cross-service contract types (100% test coverage)
- OTel library: `panakoes-otel`, OpenTelemetry SDK setup + boto3/FastAPI/httpx instrumentation

**Observability**
- CloudWatch + AWS X-Ray (OpenTelemetry via ADOT) wired end-to-end
- Cost tracking: FinOps waves 1 and 2 applied (~$15-18/mo organic cost reduction)

---

## In flight (open PRs)

- AI summarization service (Claude Haiku default, Sonnet for paid "deep summary")
- Notification service (email + webhook on transcript ready / billing events)
- Session Manager (streaming session lifecycle)
- Auth client library (`panakoes-auth-client`): shared JWT-validation for Python services
- TypeScript OTel library (`@panakoes/otel`)
- Test helpers library (`panakoes-test-helpers`)
- EventBridge bus + SNS + SQS + DLQs
- VPC endpoints (keep traffic on AWS backbone)
- AWS Backup plan (DDB + RDS)
- AWS Batch GPU compute environment + job definitions
- API Gateway HTTP + WebSocket + custom domain
- Frontend: S3 + CloudFront + ACM + Route53
- Step Functions long-audio fan-out (> 10 min files)
- Admin dashboard (SvelteKit, Tier 1 health view)

---

## v0.1 MVP target

The v0.1 release closes both the async batch transcription pipeline (S3 upload to summarized transcript) and the live streaming transcription pipeline (browser mic to partial transcript via WebSocket), plus the billing service (Stripe checkout + webhooks) and the public-facing SvelteKit web app.

Everything else is explicitly phase 2.

---

## Phase 2 (after v0.1)

- Multi-tenancy and team accounts
- Production environment (separate AWS account, proper TLS certs)
- Mobile wearable integration (AI wearable companion backend)
- Speaker diarization
- Custom vocabulary fine-tuning
- Export formats (SRT, VTT, DOCX)
- Webhook integrations (Slack, Notion, Zapier)
- SOC 2 Type II audit readiness
