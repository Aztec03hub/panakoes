# ARCH-MIGRATION.md: Architecture State and Migration Plan

**Last updated:** 2026-05-18 (post Wave-1 verification + Container Insights cut + Wave-2 T1 in flight)

This document is the single source of truth for:
1. Current infrastructure state (precise, verifiable against AWS)
2. Target architecture for each migration wave
3. Dev vs production differences
4. Migration task list with review gates
5. Orchestrator context-management guide
6. Agent dispatch templates per wave

**Companion document:** `docs/service-contracts.md` -- precise per-service boundary contracts (inputs, outputs, env vars, resource names). Read it before dispatching any agent that touches service boundaries.

---

## 1. Current Infrastructure State (2026-05-14)

### 1.1 Network

| Resource | Value |
|---|---|
| VPC CIDR | 10.10.0.0/16 |
| Availability zones | us-east-1a, us-east-1b, us-east-1c |
| Public subnets | 10.10.0.0/20, 10.10.16.0/20, 10.10.32.0/20 (IGW routed) |
| Private subnets | 10.10.48.0/20, 10.10.64.0/20, 10.10.80.0/20 (no routing -- NAT removed) |
| Internet Gateway | present |
| NAT Gateway | **REMOVED** (PR #346, 2026-05-14) |
| VPC Interface Endpoints | **REMOVED** (PR #347, 2026-05-14) |
| S3/DynamoDB Gateway Endpoints | **REMOVED** (PR #347, 2026-05-14) |

Private subnets have no internet routing and no NAT. They exist but are unused by ECS. Reserved for production-pattern reuse (see section 3).

### 1.2 ECS (Fargate)

Cluster: `panakoes-dev`

All 11 services are on **public subnets** with `assign_public_ip = true`. They reach AWS APIs (ECR, CloudWatch, Secrets Manager, S3, SQS, KMS, STS, DynamoDB) via their public IP through the Internet Gateway.

| Service | NLB name | Port | Language |
|---|---|---|---|
| ingestion-api | panakoes-dev-ingestion-api | 8000 | Python/FastAPI |
| auth | panakoes-dev-auth | 8080 | TypeScript/Hono/Better-Auth |
| admin-api | panakoes-dev-admin-api | 8000 | Python/FastAPI |
| billing | panakoes-dev-billing | 8000 | Python/FastAPI |
| cost-api | panakoes-dev-cost-api | 8000 | Python/FastAPI |
| health-aggregator | panakoes-dev-health-aggregator | 8000 | Python/FastAPI |
| session-manager | panakoes-dev-session-manager | 8000 | Python/FastAPI |
| query-api | panakoes-dev-query-api | 8000 | Python/FastAPI |
| notification | panakoes-dev-notification | 8000 | Python/FastAPI |
| summarization | panakoes-dev-summarization | 8000 | Python/FastAPI |
| gpu-spawner | panakoes-dev-gpu-spawner | 8000 | Python/FastAPI |

All NLBs are **internal** (scheme=internal), reachable only via VPC Link from API Gateway.

### 1.3 API Gateway

| Resource | Value |
|---|---|
| Type | HTTP API v2 (not REST API) |
| API names | `panakoes-dev-public` (HTTP), `panakoes-dev-streaming-ws` (WebSocket) |
| VPC Link | 1 link (`panakoes-dev-vpc-link`, ID `3kb0o5`), in private subnets (1a/1b/1c), SG `sg-031e19cbcc6c33ea3` |
| Stage | dev (auto-deploy=true) |
| URL pattern | `https://<id>.execute-api.us-east-1.amazonaws.com/dev/<route>` |
| Auth | JWT authorizer pointing to auth service `/token` endpoint |

**Current live routes (4 of 11 services wired):**

| Route | Backend NLB |
|---|---|
| `ANY /v1/auth/{proxy+}` | panakoes-dev-auth |
| `ANY /v1/admin-api/{proxy+}` | panakoes-dev-admin-api |
| `ANY /v1/cost-api/{proxy+}` | panakoes-dev-cost-api |
| `ANY /v1/health-aggregator/{proxy+}` | panakoes-dev-health-aggregator |

**API GW drift (apply never run after ECS outputs were expanded):** The ECS module's `nlb_listener_arns` output contains all 11 services. The api-gateway module is designed to auto-create routes for all discovered services, but it has not been re-applied since the ECS outputs were expanded. A plain `terraform apply` on `infra/dev/api-gateway/` would create 7 additional integrations + routes. Wave 1 should NOT run this apply -- instead, wire the new shared ALB directly for all needed services, skipping the NLB-based drift resolution entirely.

**Services with NLBs but NO current API GW route (7):** ingestion-api, query-api, session-manager, billing, summarization, notification, gpu-spawner. All 7 NLBs are live and billing at ~$18/mo each with zero active traffic through them.

**Internal-only services (no API GW route needed, ever):** summarization (triggered by SQS/Step Functions), notification (triggered internally by other services), gpu-spawner (triggered by session-manager). These 3 should be wired via ECS Service Connect for service-to-service calls, not API GW.

**Note:** HTTP API v2 cannot have WAFv2 WebACLs attached (regional WAFv2 only works with REST API or ALB). WAF removed in PR #347.

### 1.4 Data Layer

| Resource | Type | Details |
|---|---|---|
| panakoes-dev-auth-rds | RDS Postgres 16 | db.t4g.micro, single-AZ, private subnets |
| DynamoDB tables | On-demand | See `infra/dev/data/main.tf` for full list |
| S3: panakoes-dev-audio-uploads | Standard | Audio file ingestion |
| S3: panakoes-dev-transcripts | Standard | Transcription output |
| S3: panakoes-dev-log-archive | Standard + Glacier transitions | Long-term log storage |

### 1.5 KMS Keys (19 CMKs, $19/month)

| Alias | Purpose |
|---|---|
| panakoes-dev-api-gateway-logs | API GW CloudWatch log encryption |
| panakoes-dev-audio-uploads | S3 audio bucket SSE |
| panakoes-dev-auth-db-rds | RDS storage encryption |
| panakoes-dev-backup | AWS Backup vault |
| panakoes-dev-cost-rollup-aggregator-log | Cost aggregator CloudWatch logs |
| panakoes-dev-ecr | ECR image encryption |
| panakoes-dev-events | SNS/SQS event bus encryption |
| panakoes-dev-frontend | CloudFront/S3 frontend encryption |
| panakoes-dev-jwt-signing | JWT key signing (critical, separate key) |
| panakoes-dev-log-archive | Log archive S3 bucket |
| panakoes-dev-logs | CloudWatch Logs default |
| panakoes-dev-long-audio-sfn-logs | Step Functions execution log |
| panakoes-dev-secrets | Secrets Manager |
| panakoes-dev-security | Security Hub / GuardDuty |
| panakoes-dev-streaming-ws-logs | WebSocket streaming logs |
| panakoes-dev-transcribe-trigger | Transcription trigger queue |
| panakoes-dev-transcribe-worker-log | Transcription worker logs |
| panakoes-dev-transcripts | Transcripts S3 bucket |
| panakoes-tf-state | Terraform remote state S3 bucket |

### 1.6 Active Terraform Modules (post Wave-1 + PR #365 in flight)

```
infra/dev/
  admin-state/        # S3 state for admin SPA deployment
  alb/                # Shared internal ALB (W1-T2; replaced 11 NLBs)
  api-gateway/        # HTTP API v2, VPC links, routes, JWT authorizer
  api-gateway-domain/ # Custom domain mapping (panakoes.com)
  api-gateway-ws/     # WebSocket API for streaming
  auth-db-rds/        # RDS Postgres 16 for auth service
  auth-kms-signing/   # Dedicated KMS key for JWT signing
  backup/             # AWS Backup vault + daily plan
  batch/              # AWS Batch (GPU transcription jobs)
  budgets/            # AWS Budgets alerts
  cost-anomaly-monitor/ # Cost Anomaly Detection
  cost-rollup-aggregator/ # Lambda: daily cost aggregation to DDB
  data/               # DynamoDB tables (audit, ingestion, sessions, etc.)
  ecr/                # ECR repos (one per service)
  ecs/                # ECS cluster + Fargate task defs + 11 services (NLBs removed W1-T5; Container Insights off 2026-05-18)
  events/             # SNS topics + SQS queues (event bus)
  frontend/           # S3 + CloudFront for admin SPA
  iam/                # IAM roles + task execution policies
  kms/                # Consolidated CMKs panakoes/app-data + panakoes/logs (W2-T1, PR #365 in flight)
  network/            # VPC, subnets, IGW, flow logs (NAT GW removed W0)
  observability/      # CloudWatch dashboards, X-Ray, ADOT
  secrets/            # Secrets Manager (per-service secrets)
  security/           # Security Hub, GuardDuty, Config
  service-discovery/  # Cloud Map private DNS namespace panakoes-dev.local (W1-T1)
  ses/                # SES email sending (notification service)
  step-functions/     # Long-audio fan-out state machine
  storage/            # S3 buckets (audio, transcripts, logs)
  transcribe-worker/  # AWS Batch job definition for Whisper
```

**Removed since 2026-05-14 cost-reduction session:** `vpc-endpoints/` (16 interface endpoints destroyed), `waf/` + `cloudfront-waf/` (both destroyed; 0 resources were attached), `auth-db/` (Aurora cluster destroyed; superseded by `auth-db-rds/`).

### 1.7 Cost Profile (verified live, post-Wave-1 + Container Insights cut)

Refreshed 2026-05-18 against live `aws ce get-cost-and-usage` data, May 15-18 steady-state, projected to monthly:

| Item | Monthly /mo | Notes |
|---|---:|---|
| ECS Fargate compute | ~$39 | 7 healthy tasks on ARM64 (4 failed services scaled to desired=0 on 2026-05-18; see below) |
| Public IPv4 on tasks | ~$20 | 7 tasks × $0.005/hr × 730 (NAT replacement cost from Wave 0) |
| ALB (1 internal) | ~$14 | Replaced 11 NLBs ($198/mo) in Wave 1 |
| KMS CMKs (19 still) | ~$15 | Wave 2 T1 in flight (PR #365); -$13/mo when W2-T7 retires 15 old keys |
| RDS t4g.micro | ~$11 | as designed |
| Route 53 (Cloud Map zone) | ~$4 | `panakoes-dev.local` namespace created in W1-T1 |
| Secrets Manager (9 secrets) | ~$3 | as designed |
| EBS snapshots | ~$3 | 1 × 100GB GPU AMI bake (`ami-0dee04ee5042c94cf`, KEEP, pinned in batch + gpu_spawner) |
| ECR + S3 + misc | ~$1 | rounding |
| **Total gross** | **~$109/mo** | (was ~$153/mo pre 2026-05-18; ~$503/mo pre Wave 0) |
| AWS Activate Founders credits | absorbing | (~-$84 in May to date) |
| **Net cost** | **$0** | as long as credits last |

**Container Insights disabled 2026-05-18 (PR #363):** previously $44/mo (~30% of dev bill) from 233 paid CW metrics enabled by PR #344 to power the health-aggregator dashboard. Basic ECS CPU/memory remains visible via the ECS API. Re-enable in production once observability needs and steady-state metric volume are known.

**Failed ECS services scaled to desired=0 (2026-05-18, CLI):** `billing` (Stripe key crashes on startup), `gpu-spawner` (no ECR image), `ingestion-api` (no ECR image), `query-api` (no ECR image). Each consumes nothing while at 0; revive by fixing the underlying issue (image bake or Stripe key wiring), then `aws ecs update-service --desired-count 1`.

**Budget alert note:** AWS Budget at $100/mo still tracks gross Usage charges, not net-of-credits. At ~$109/mo gross it will close to budget. Two options: (1) raise the budget threshold in the console, or (2) filter the budget by `RECORD_TYPE != Credit` to track net. Either way, Phil owns the console-side change.

---

## 2. Target Architecture

### 2.1 Wave 1 Target: Zero NLBs (ECS Service Connect + shared ALB)

**Goal:** Replace 11 internal NLBs (~$198/mo) with one internal ALB ($16/mo) for API GW integration, plus ECS Service Connect for service-to-service traffic.

**Architecture:**

```
Internet
  |
  v
API Gateway HTTP API v2
  |   (VPC Link)
  v
ONE internal Application Load Balancer
  |   (path-based routing rules)
  +---> /v1/auth/*     --> auth target group     (port 3000)
  +---> /v1/ingest/*   --> ingestion target group (port 8000)
  +---> /v1/billing/*  --> billing target group   (port 8000)
  +---> /v1/query/*    --> query-api target group (port 8000)
  +---> /v1/admin/*    --> admin-api target group (port 8000)
  +---> /v1/session/*  --> session-manager TG     (port 8000)
  +---> /v1/notify/*   --> notification TG        (port 8000)
  +---> /v1/cost/*     --> cost-api TG            (port 8000)
  +---> /v1/summary/*  --> summarization TG       (port 8000)
  +---> /v1/gpu/*      --> gpu-spawner TG         (port 8000)
  +---> /healthcheck   --> health-aggregator TG   (port 8000)

Internal service-to-service:
  ingestion-api --> auth.panakoes-dev.local:3000 (Service Connect)
  billing       --> auth.panakoes-dev.local:3000 (Service Connect)
  summarization --> ingestion-api.panakoes-dev.local:8000 (Service Connect)
```

**Service Connect namespace:** `panakoes-dev.local`

Each ECS service registers itself with a DNS name: `<service-name>.panakoes-dev.local:<port>`. Traffic is load-balanced by ECS Service Connect at the sidecar layer with no extra cost.

**ALB vs NLB choice:** ALB is required for path-based routing (NLB only routes by port). One ALB replaces 11 NLBs. ALB also enables header-based and query-string routing if needed.

**VPC Link change:** Current VPC Links are NLB-targeted (one per NLB). New VPC Link is ALB-targeted (one link to the shared ALB). HTTP API v2 supports ALB-targeted VPC Links since 2022.

**Savings:** $198 - $16 = $182/mo.

### 2.2 Wave 2 Target: KMS Consolidation (19 CMKs to 4)

**Goal:** Replace 19 CMKs ($19/mo) with 4 operational keys ($4/mo).

**Key mapping:**

| New key alias | Replaces | Used by |
|---|---|---|
| `panakoes/jwt-signing` | panakoes-dev-jwt-signing (keep separate, security requirement) | auth service JWT signing |
| `panakoes/app-data` | panakoes-dev-audio-uploads, panakoes-dev-transcripts, panakoes-dev-auth-db-rds, panakoes-dev-events, panakoes-dev-secrets, panakoes-dev-backup, panakoes-dev-ecr, panakoes-dev-frontend, panakoes-dev-security, panakoes-dev-streaming-ws-logs, panakoes-dev-transcribe-trigger, panakoes-dev-log-archive | S3, RDS, SQS, SNS, Secrets Manager, ECR |
| `panakoes/logs` | panakoes-dev-logs, panakoes-dev-api-gateway-logs, panakoes-dev-cost-rollup-aggregator-log, panakoes-dev-long-audio-sfn-logs, panakoes-dev-transcribe-worker-log | CloudWatch Logs groups |
| `panakoes-tf-state` | panakoes-tf-state (keep separate, bootstrap key) | Terraform state S3 bucket |

JWT signing key MUST remain separate (security: compromise of app-data key must not enable JWT forgery).
TF state key MUST remain separate (bootstrap dependency: if consolidated, a single-key compromise could expose all state).

**Re-encryption plan:**
1. Create 2 new CMKs (`panakoes/app-data`, `panakoes/logs`) via Terraform
2. S3 buckets: update bucket encryption config to new key -- AWS re-encrypts in place on next write (no downtime)
3. Secrets Manager: copy each secret with `--kms-key-id` pointing to new key
4. RDS: use `modify-db-instance --storage-encrypted` with new key (requires reboot window)
5. ECS task defs: update `kmsKeyId` references in task definitions
6. Schedule old keys for deletion with 7-day window
7. Verify no service uses old key ARNs (search task defs, Terraform state)

**Savings:** $19 - $4 = $15/mo.

### 2.3 Long-Term Production Target (deferred, do not implement in dev)

Production will differ from dev in these ways (see section 3). No Terraform work needed until pre-launch. Documented here for architecture continuity.

---

## 3. Dev vs Production Differences

| Dimension | Dev (current) | Production (target) |
|---|---|---|
| ECS subnet placement | Public subnets, `assign_public_ip=true` | Private subnets, no public IPs |
| NAT Gateway | None | One per AZ (3x, HA) |
| RDS | t4g.micro, single-AZ | t4g.small or t4g.medium, Multi-AZ |
| ECS task count | 1 per service | 2+ per service (min for HA) |
| ALB | Internal, 1 shared | Internal, 1 shared (same pattern) |
| WAF | None (removed) | WAFv2 on ALB (NOT API GW v2) |
| CloudFront + WAF | None for API | CloudFront in front of API GW + WAFv2 on CF |
| Log retention | 30 days CW, S3 archive | 90 days CW, 365+ days S3 |
| KMS keys | 4 (post Wave 2) | 4 (same -- no difference needed) |
| ECS auto-scaling | Not configured | Target-tracking on CPU + request count |
| Backup retention | 30 days | 365 days with cross-region copy |
| VPC Flow Logs | CloudWatch 30 days | S3 + Athena (cost-efficient at scale) |
| GPU instance type | g4dn.xlarge Spot | g4dn.xlarge Spot with On-Demand fallback |
| API GW throttle | Default | Configured per-route limits |
| Monitoring | CloudWatch basic | CloudWatch + X-Ray + Canary synthetics |

**Key insight for orchestrator:** the dev architecture deliberately trades security posture for cost. ECS on public subnets with public IPs is a dev convenience, not a production pattern. Every agent working on ECS networking must know which environment it is targeting. Never propose "move ECS back to private subnets" as a cost-saving measure -- it was a cost-saving measure TO move them to public subnets. The reverse is a production hardening step, not a regression.

---

## 4. Migration Task List

### Wave 0: Cost Reduction (COMPLETED 2026-05-14)

**Review checkpoint:** COMPLETED. All items shipped.

| Task | PR | Status |
|---|---|---|
| Move ECS to public subnets, remove NAT GW | #346 | MERGED + APPLIED |
| Destroy VPC Interface Endpoints (16 + SG) | #347 | MERGED + APPLIED |
| Destroy WAF (regional + global) | #347 | MERGED + APPLIED |
| Destroy Aurora auth-db | #347 | MERGED + APPLIED |
| Clean dead Terraform refs (backup + ecs) | #348 | MERGED + APPLIED |
| Extend local dev stack (LocalStack + init) | #349 | MERGED + APPLIED |
| Document architecture + service contracts | #350, #351 | MERGED |

**Wave 0 reconfirmation result (verified 2026-05-18 against live AWS):** 0 NLBs, 0 NAT gateways, 0 VPC interface/gateway endpoints, 0 WAF WebACLs. ECS services on public subnets reachable via public IPv4. Local stack (LocalStack + dynamodb-local + postgres) verified working on new dev machine 2026-05-18.

### Wave 1.5: Container Insights + idle service cleanup (COMPLETED 2026-05-18)

Unanticipated cost-reduction wave between Wave 1 and Wave 2. Surfaced during post-Wave-1 cost audit on a new dev machine. CloudWatch Container Insights (enabled by PR #344 to power the health-aggregator dashboard) was emitting 233 paid metrics at ~$44/mo, ~30% of the post-Wave-1 dev bill.

| Task | PR / CLI | Status | $/mo saved |
|---|---|---|---|
| Disable ECS Container Insights on `panakoes-dev` cluster | #363 | MERGED + APPLIED 2026-05-18 | ~$44 |
| Scale 4 failing services to `desired_count = 0` via CLI (billing, gpu-spawner, ingestion-api, query-api; all pre-existing image/config bugs) | CLI only | LIVE | ~$5 |

Health-aggregator dashboard retains task-level CPU/memory via ECS DescribeServices/DescribeTasks API. Re-enable Container Insights in production when steady-state metric volume is known.

---

### Wave 1: Zero NLBs (ECS Service Connect + Shared ALB)

**Estimated savings:** $182/mo
**Estimated effort:** 1-2 sessions, 3-4 parallel agents
**Pre-wave review required:** read Wave 0 reconfirmation above. Confirm PR #349 merged and `make test-local` passes on Phil's machine before starting.

**Pre-wave reconfirmation checklist (orchestrator runs before dispatching Wave 1 agents):**
- [ ] `aws elbv2 describe-load-balancers --query 'LoadBalancers[?Scheme==\`internal\`]'` shows 11 NLBs (verified 2026-05-14: health-aggregator, ingestion-api, session-manager, notification, summarization, gpu-spawner, admin-api, billing, cost-api, auth, query-api)
- [ ] `aws ecs list-services --cluster panakoes-dev` shows 11 services
- [ ] `aws apigatewayv2 get-vpc-links` shows 1 VPC link (`panakoes-dev-vpc-link`, ID `3kb0o5`)
- [ ] `aws servicediscovery list-namespaces` returns empty (verified 2026-05-14: clean slate)
- [ ] `make test-local` passes (validates local stack before any infra change)

**Services and their Wave 1 routing target:**

| Service | Current API GW route | Wave 1 ALB route | Notes |
|---|---|---|---|
| auth | YES `/v1/auth` | YES | User-facing: sign-in/sign-up |
| admin-api | YES `/v1/admin-api` | YES | User-facing: admin dashboard |
| cost-api | YES `/v1/cost-api` | YES | User-facing: cost data |
| health-aggregator | YES `/v1/health-aggregator` | YES | User-facing: service health |
| ingestion-api | NO (drift) | YES `/v1/ingestion-api` | User-facing: audio uploads |
| query-api | NO (drift) | YES `/v1/query-api` | User-facing: transcript queries |
| session-manager | NO (drift) | YES `/v1/session-manager` | User-facing: streaming sessions |
| billing | NO (drift) | YES `/v1/billing` | Stripe webhooks need public URL |
| summarization | NO | NO | Internal: SQS/Step Functions triggered |
| notification | NO | NO | Internal: triggered by other services |
| gpu-spawner | NO | NO | Internal: triggered by session-manager |

**Tasks:**

| Task ID | Description | Files touched | Agent |
|---|---|---|---|
| W1-T1 | Create Cloud Map namespace `panakoes-dev.local` | infra/dev/service-discovery/ (new module) | Agent A |
| W1-T2 | Create shared internal ALB + 11 target groups + path-based listener rules | infra/dev/alb/ (new module) | Agent B |
| W1-T3 | Update API GW to target shared ALB; add 4 new routes (ingestion-api, query-api, session-manager, billing); remove 7 obsolete NLB integrations | infra/dev/api-gateway/main.tf | Agent C (after W1-T2 applied) |
| W1-T4 | Add ECS Service Connect config to all 11 services; remove `aws_vpc_security_group_ingress_rule.*_task_from_vpc_link` for 3 internal services | infra/dev/ecs/*.tf | Agent D (after W1-T1 applied) |
| W1-T5 | Remove all 11 NLBs, NLB listeners, and NLB target groups from ECS service TF files; remove the `nlb_listener_arns` output block from outputs.tf | infra/dev/ecs/*.tf, infra/dev/ecs/outputs.tf | Agent E (after W1-T3 + W1-T4 verified, no traffic errors) |
| W1-T6 | Verify all 8 public routes through ALB return HTTP 200; verify 3 internal services respond to Service Connect DNS | curl + CloudWatch | Orchestrator |
| W1-T7 | Update `docs/service-contracts.md` with new API GW routes and Service Connect DNS names | docs/service-contracts.md | Agent F (after W1-T6) |

**Wave 1 review checkpoint (after all tasks complete):**
- [ ] `aws elbv2 describe-load-balancers --query 'LoadBalancers[?Type==\`network\`]'` returns empty
- [ ] `aws elbv2 describe-load-balancers --query 'LoadBalancers[?Type==\`application\`]'` shows 1 ALB
- [ ] ALB has 11 target groups: `aws elbv2 describe-target-groups --load-balancer-arn <arn>`
- [ ] All 8 public routes verified via curl (stage-prefixed: `/dev/v1/<service>/health`)
- [ ] Service Connect DNS resolves within ECS: `<svc>.panakoes-dev.local`
- [ ] No 5xx spike in CloudWatch for 5 minutes after cutover
- [ ] `make test-local` still passes
- [ ] Monthly cost recalculation: 11 NLBs removed ($198/mo) + 1 ALB added (~$16/mo) = ~$182/mo net savings

**Wave 1 critical agent constraints:**
- Agent D must NOT remove NLB Terraform resources (only adds Service Connect). NLB removal is W1-T5.
- Agent E (NLB removal) must only run AFTER Agent C (API GW updated + applied) AND W1-T6 curl verification shows no errors.
- Agents A and B can run in parallel. Agent C blocks on B. Agent D blocks on A. Agent E blocks on C+D+T6.
- **Do NOT apply `infra/dev/api-gateway/` before Wave 1 to resolve the drift.** Doing so would create 7 NLB-based integrations that we immediately remove in W1-T3. Skip the drift resolution -- go straight to ALB wiring.

---

### Wave 2: KMS Consolidation

**Estimated savings:** $15/mo (net, once old 15 keys retire in W2-T7)
**Estimated effort:** 0.5-1 session, 2-3 agents
**Pre-wave review required:** Wave 1 review checkpoint completed (DONE 2026-05-14).

**Pre-wave reconfirmation result (2026-05-18):**
- [x] 19 panakoes-dev-* KMS aliases verified live + `panakoes-tf-state` + 1 cross-project `lafayettelabs-cloudtrail` (which stays).
- [x] No service errors in CloudWatch baseline (Container Insights now off; basic ECS health all green).
- [x] `docs/service-contracts.md` current as of 2026-05-14.

**Tasks:**

| Task ID | Description | Files touched | Agent | Status |
|---|---|---|---|---|
| W2-T1 | Create new CMKs: `alias/panakoes/app-data` + `alias/panakoes/logs` | `infra/dev/kms/` (new module) | Agent A | **DISPATCHED 2026-05-18 (PR #365). Plan = 4 add / 0 destroy. Auto-merge armed.** |
| W2-T2 | Update S3 bucket encryption configs to `panakoes/app-data` | infra/dev/storage/main.tf, infra/dev/frontend/main.tf | Agent B (after W2-T1 applied) | PENDING |
| W2-T3 | Update Secrets Manager, SQS, SNS, ECR, Backup to `panakoes/app-data` | infra/dev/secrets/, infra/dev/events/, infra/dev/ecr/, infra/dev/backup/ | Agent B (same PR) | PENDING |
| W2-T4 | Update CloudWatch Logs KMS refs to `panakoes/logs` | infra/dev/observability/, infra/dev/api-gateway/, etc. | Agent C (after W2-T1 applied) | PENDING |
| W2-T5 | Update RDS to use `panakoes/app-data` | infra/dev/auth-db-rds/main.tf | Agent B (same PR) | PENDING |
| W2-T6 | Update ECS task definitions to remove per-service CMK refs | infra/dev/ecs/*.tf | Agent D (after W2-T1 applied) | PENDING |
| W2-T7 | Schedule old 15 CMKs for deletion (7-day window via AWS CLI) | n/a -- CLI only, no Terraform | Orchestrator (not an agent -- destructive) | PENDING |
| W2-T8 | Verify no errors 24h after key rotation | CloudWatch alarm review | Orchestrator | PENDING |

**W2-T1 design notes (per agent's run report at `.agent-runs/2026-05-18T19-39-22Z-w2-t1-kms-new-keys.md`):**

- Alias naming uses fixed `panakoes/` prefix (not env-templated `panakoes-dev-*`). The new aliases are a cross-PR contract; downstream modules will reference these names. Prod will use the same alias names under a separate AWS account / keystore rather than reuse dev's.
- Logs-key `ArnLike` condition is broad (`arn:aws:logs:us-east-1:659225405128:log-group:*`) rather than scoped to a single naming prefix. The consolidated logs key serves multiple modules; tight scoping would force key-policy updates on every new log-group pattern. Use is still scoped to this account and region.
- Both keys: `enable_key_rotation = true`, `multi_region = false`, `deletion_window_in_days = 30`, symmetric `ENCRYPT_DECRYPT`.

**Wave 2 review checkpoint:**
- [ ] `aws kms list-aliases` shows only 4 panakoes CMKs
- [ ] `aws kms list-keys` + `aws kms describe-key` confirms old keys in `PendingDeletion` state
- [ ] S3 bucket encryption: `aws s3api get-bucket-encryption` returns new key ARN
- [ ] Secrets Manager: `aws secretsmanager describe-secret --secret-id <name>` shows new key ARN
- [ ] RDS: `aws rds describe-db-instances` shows new KMS key
- [ ] No CloudWatch alarm for 24h post-rotation
- [ ] Monthly cost: ~$98 - $15 = ~$83/mo gross

### Wave 2 Cleanup Chores (bundle with Wave 2 or as standalone PR)

| Task | Description | File | Notes |
|---|---|---|---|
| W2-C1 | Migrate all 7 DynamoDB tables from `hash_key`/`range_key` to `key_schema` | infra/dev/data/main.tf | AWS provider v6 emits 13 deprecation warnings against current tables. Pre-existing issue, not introduced by any specific PR. Migrate all tables in a single PR to clear the warnings. |

---

### Wave 3: Production Scaffolding (deferred -- do not start without Phil's decision)

This wave is not planned for execution. It exists to document the path so future sessions inherit the intent.

**Trigger:** pre-launch production environment preparation. Phil signals this with explicit instruction.

**Key tasks (sketch, not detailed):**
- Move ECS back to private subnets (security)
- Add 3x NAT GW (one per AZ, HA)
- RDS to Multi-AZ (t4g.small or t4g.medium)
- Add CloudFront distribution in front of API GW
- Add WAFv2 on ALB (not API GW v2 -- architectural constraint from Wave 1)
- ECS auto-scaling (target-tracking)
- Separate AWS account for production (Account Vending Machine or Control Tower)
- ECS tasks at desired_count=2+ per service
- Secrets Manager cross-account replication

---

## 5. Wave Review Protocol

The orchestrator (main Claude context) MUST execute this protocol at each wave boundary. Do not delegate this to a sub-agent -- it requires synthesizing across all agent run reports.

### Pre-Wave Review (before dispatching any agent for wave N)

1. Read all `.agent-runs/*.md` from wave N-1 that are not yet reviewed.
2. For each report: confirm `status: success`, check `files_created/files_modified` match `git diff main`.
3. Run the wave N-1 reconfirmation checklist from this document against live AWS state.
4. If any checklist item fails: spawn a fix agent before proceeding. Do not start wave N with broken state.
5. Update this document's wave N-1 section with "COMPLETED [date]" and any deviations found.

### Post-Wave Review (after all wave N tasks are merged)

1. Execute the wave N review checkpoint from this document.
2. Recalculate actual monthly cost from live AWS resources (not estimates).
3. Note any services that had unexpected behavior during the wave (timeouts, errors, IAM denials).
4. Update `docs/service-contracts.md` if any interface changed.
5. Update this document: mark wave N complete, record actual savings vs estimate, log deviations.

### Mid-Wave Agent Failure Handling

If an agent's run report has `status: failure` or a CI check fails:
1. Read the run report's "Issues Encountered" and "Rollback Procedure" sections.
2. Assess: is the failure safe to retry? Or did partial AWS state change?
3. If partial state change: run the rollback procedure before any retry.
4. Fix the root cause (not the symptom) before re-dispatching.
5. Log the failure in this document under the relevant task row.

---

## 6. Orchestrator Context Management Guide

### Context budget

Each wave involves multiple agents whose run reports can be 2-5 KB each. Loading all reports in the main context at once can cause compaction mid-review. Strategy:

1. **Before a wave:** load only this document (ARCH-MIGRATION.md) + the checklist section for the upcoming wave.
2. **During a wave:** do not load agent run reports inline. Use `Read` with `offset`/`limit` to extract only the "Summary" and "Issues Encountered" sections. Full context on the report only if a check failed.
3. **Post-wave review:** load reports one at a time, extract findings, write them into this document's wave section, then discard the report from context.
4. **Service contracts:** load `docs/service-contracts.md` only when dispatching agents that cross service boundaries. Load only the relevant service's section, not the full file.
5. **FOLLOWUPS.md:** only load at session start and session end. Do not keep it in context during wave execution.

### Agent dispatch parallelism rules

From CLAUDE.md: parallel agents MUST use separate worktrees. Before any wave dispatch:

```bash
# Check for task overlap before dispatching
python3 scripts/check-agent-overlap.py <brief1.md> <brief2.md>

# Create worktrees (always from repo root, always from origin/main)
cd ~/projects/panakoes
git worktree add ../panakoes-<task-slug> -b feat/<task-slug> origin/main
```

Maximum 4 parallel agents in flight at once on this project (disk constraint: each worktree ~1 GB with node_modules + .terraform).

### When to stop and ask Phil

Stop and surface to Phil when:
- A wave reconfirmation checklist item fails and the fix requires destroying a resource Phil has not explicitly approved.
- An agent's rollback procedure involves data loss (e.g., a Secrets Manager secret was deleted).
- A wave's actual cost savings deviate by >25% from the estimate in this document.
- Wave 3 is about to start (explicit Phil decision required before production work).
- Any agent finds a security-relevant misconfiguration (IAM over-grant, open security group, public S3 bucket).

---

## 7. Agent Dispatch Templates

### Wave 1: Create Cloud Map Namespace (W1-T1)

```
WORKING DIRECTORY: ~/projects/panakoes-<slug>
PREREQUISITE: Read ~/projects/panakoes-<slug>/CLAUDE.md and ~/projects/panakoes-<slug>/ARCH-MIGRATION.md (section 2.1).

TASK: Create the AWS Cloud Map namespace `panakoes-dev.local` for ECS Service Connect.

Create a new Terraform module at `infra/dev/service-discovery/main.tf` with:
- `aws_service_discovery_private_dns_namespace` named `panakoes-dev.local` in the VPC from `data.terraform_remote_state.network.outputs.vpc_id`
- Providers, variables, outputs matching the pattern in infra/dev/auth-db-rds/ (read that module as the template)
- Output: `namespace_id` and `namespace_arn`

ACCEPTANCE CRITERIA:
- terraform validate + terraform fmt clean
- terraform plan shows exactly: 1 resource to add (the namespace), no changes to other modules
- Module follows the same providers/variables/backend pattern as auth-db-rds

CONSTRAINTS:
- Do NOT add ECS Service Connect config to any service (that is W1-T4)
- Do NOT touch infra/dev/ecs/

EXPECTED FILES: infra/dev/service-discovery/main.tf, outputs.tf, variables.tf, providers.tf, README.md, .changelog/<ts>-service-discovery-namespace.md
```

### Wave 1: Create Shared ALB (W1-T2)

```
WORKING DIRECTORY: ~/projects/panakoes-<slug>
PREREQUISITE: Read CLAUDE.md, ARCH-MIGRATION.md section 2.1, and docs/service-contracts.md (all services section).

TASK: Create a shared internal Application Load Balancer replacing 11 internal NLBs.

Create a new Terraform module at `infra/dev/alb/`:
- `aws_lb` type=application, internal=true, in `data.terraform_remote_state.network.outputs.private_subnet_ids`
- Security group allowing 443 + 80 from VPC CIDR (10.10.0.0/16) only
- One `aws_lb_listener` on port 80 (HTTP) -- no TLS needed for internal-only
- 11 `aws_lb_target_group` resources (one per service) with:
  - Protocol: HTTP
  - Port: 8000 for all Python services, 3000 for auth
  - Target type: ip (ECS Fargate tasks register by IP)
  - Health check path: /health (all services expose this)
- 11 `aws_lb_listener_rule` resources with path conditions matching the API routes per `docs/service-contracts.md`
- Output: `alb_arn`, `alb_dns_name`, `target_group_arns` (map from service name to ARN)

The path routing rules are in docs/service-contracts.md under each service's "API GW route prefix" field.

ACCEPTANCE CRITERIA:
- terraform validate + fmt clean
- terraform plan: ALB + SG + listener + 11 TGs + 11 rules = ~26 resources
- No changes to infra/dev/ecs/ or infra/dev/api-gateway/ (those are W1-T4 and W1-T3)

EXPECTED FILES: infra/dev/alb/*.tf, .changelog/<ts>-shared-alb.md
```

### Wave 2: KMS Consolidation - New Keys (W2-T1)

```
WORKING DIRECTORY: ~/projects/panakoes-<slug>
PREREQUISITE: Read CLAUDE.md, ARCH-MIGRATION.md section 2.2.

TASK: Create 2 new KMS CMKs that will consolidate 15 old keys.

Create a new Terraform module at infra/dev/kms/ OR add to an existing appropriate module:
- CMK alias `panakoes/app-data`: multi-region=false, enable_key_rotation=true
  Key policy: grants ECS task roles, S3, Secrets Manager, RDS, SQS, SNS, ECR, Backup
- CMK alias `panakoes/logs`: multi-region=false, enable_key_rotation=true
  Key policy: grants CloudWatch Logs service principal for log group encryption

Do NOT delete or modify any existing CMK. Do NOT update any services to use the new keys yet (that is W2-T2 through W2-T6).

ACCEPTANCE CRITERIA:
- terraform plan: exactly 2 new aws_kms_key + 2 aws_kms_alias = 4 resources
- Existing panakoes-dev-* keys: 0 changes
- Key policies follow least-privilege (no wildcards on Principal)

EXPECTED FILES: infra/dev/kms/*.tf (or additions to existing module), .changelog/<ts>-kms-new-keys.md
```

---

## 8. Service Boundary Contract Reference

**The definitive per-service contracts live in `docs/service-contracts.md`.** That file is generated from actual service source code and is authoritative. This section lists only the cross-service communication topology so orchestrators can reason about agent dispatch order without loading the full contracts file.

### Internal call graph (service → service)

```
ingestion-api  --> auth (JWT verification)
billing        --> auth (JWT verification)
query-api      --> auth (JWT verification)
admin-api      --> auth (JWT verification)
session-manager--> auth (JWT verification)
notification   --> auth (JWT verification)
summarization  --> auth (JWT verification)

ingestion-api  --> session-manager (session state updates)
gpu-spawner    --> ingestion-api (transcription complete callback)
summarization  --> ingestion-api (summary complete callback)
health-aggregator --> (reads CloudWatch, ECS API -- no service-to-service calls)
cost-api       --> (reads Cost Explorer, DynamoDB -- no service-to-service calls)
```

### AWS resource ownership (who creates vs who reads)

| Resource | Owner (creates) | Readers |
|---|---|---|
| `panakoes-dev-audio-uploads` S3 | ingestion-api writes | transcribe-worker reads |
| `panakoes-dev-transcripts` S3 | transcribe-worker writes | summarization reads |
| `panakoes-dev-ingestion` DDB | ingestion-api writes | query-api reads |
| `panakoes-dev-audit-log` DDB | all services write | admin-api reads |
| `panakoes-dev-streaming-sessions` DDB | session-manager writes/reads | gpu-spawner reads |
| `panakoes-dev-audio-uploaded` SQS | ingestion-api enqueues | transcribe-worker dequeues |
| `panakoes-dev-billing-events` SNS | billing publishes | (subscribers TBD) |
| RDS `panakoes_auth` DB | auth service (migrations + reads/writes) | (auth only) |

---

## 9. Terraform Remote State Dependencies

Modules that read other modules' state via `data.terraform_remote_state`:

```
api-gateway      <- network (vpc_id, subnet_ids)
api-gateway      <- ecs (nlb_dns_names -- will change in Wave 1)
ecs              <- network (subnet_ids)
ecs              <- ecr (repository_urls)
ecs              <- data (table_names, table_arns)
ecs              <- events (queue_urls, topic_arns)
ecs              <- secrets (secret_arns)
ecs              <- auth-db-rds (endpoint, port)
ecs              <- auth-kms-signing (key_arn)
backup           <- data (table_arns)
backup           <- auth-db-rds (cluster_arn removed in PR #348)
iam              <- data (table_arns)
iam              <- storage (bucket_arns)
iam              <- events (queue_arns, topic_arns)
iam              <- ecr (repository_arns)
```

**For Wave 1:** the `api-gateway` module reads NLB DNS names from ECS state. This output must be updated to the ALB DNS name from the new `alb` module before the VPC Link can be switched. Agents touching api-gateway or ecs state outputs must account for this dependency.

---

## 10. Glossary for Fresh Claude Instances

| Term | Meaning |
|---|---|
| VPC Link | API GW mechanism for routing to private VPC resources (NLBs or ALBs) |
| Service Connect | ECS feature that provides service-to-service discovery and load balancing within a cluster via Cloud Map namespace; zero additional cost |
| Cloud Map | AWS service registry for DNS-based service discovery. Creates `<name>.<namespace>` DNS records ECS tasks resolve internally |
| assign_public_ip | ECS Fargate option to assign a public IP to each task. Required for tasks in public subnets to reach the internet (ECR, S3, CW) without NAT GW |
| HTTP API v2 | AWS API Gateway v2 (not REST API v1). Cheaper, faster, but cannot attach WAFv2 WebACLs (WAF only works on REST API v1 or ALB or CloudFront) |
| CMK | Customer-managed KMS key. Costs $1/month. AWS-managed keys (aws/s3 etc.) are free. |
| Activate Founders | AWS Activate Founders credits ($1,000 typically). Panakoes account has these applied. Net cost is $0 but budget alarms fire on gross Usage charges before credits are applied. |
| Spot interruption | AWS can reclaim Spot instances with 2-minute warning. GPU Spot instances (g4dn.xlarge) have ~5% interruption rate in us-east-1. Session-spawned GPU is expected to be ~10-20 min; interruption during session = failed transcription, auto-retry. |
| panakoes-admin | Local AWS CLI profile name for Phil's IAM admin user. All CLI commands use `--profile panakoes-admin`. Account ID: 659225405128. |
