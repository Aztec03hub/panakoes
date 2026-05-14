# ARCH-MIGRATION.md: Architecture State and Migration Plan

**Last updated:** 2026-05-14 (post cost-reduction session)

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
| auth | panakoes-dev-auth | 3000 | TypeScript/Hono/Better-Auth |
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
| Name | panakoes-dev |
| VPC Link | one VPC link per NLB (11 links) |
| Stage | dev (auto-deploy=true) |
| URL pattern | `https://<id>.execute-api.us-east-1.amazonaws.com/dev/<route>` |
| Auth | JWT authorizer pointing to auth service `/token` endpoint |

API GW routes: all `ANY /{proxy+}` patterns forwarded to the appropriate NLB via VPC Link.

**Note:** HTTP API v2 cannot have WAFv2 WebACLs attached (regional WAFv2 only works with REST API or ALB). This was the root cause of the WAF never actually protecting anything -- it was attached to nothing. WAF was removed (PR #347).

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

### 1.6 Active Terraform Modules (post PR #347)

```
infra/dev/
  admin-state/        # S3 state for admin SPA deployment
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
  ecs/                # ECS cluster + Fargate task defs + 11 services + 11 NLBs
  events/             # SNS topics + SQS queues (event bus)
  frontend/           # S3 + CloudFront for admin SPA
  iam/                # IAM roles + task execution policies
  network/            # VPC, subnets, IGW, flow logs
  observability/      # CloudWatch dashboards, X-Ray, ADOT
  secrets/            # Secrets Manager (per-service secrets)
  security/           # Security Hub, GuardDuty, Config
  ses/                # SES email sending (notification service)
  step-functions/     # Long-audio fan-out state machine
  storage/            # S3 buckets (audio, transcripts, logs)
  transcribe-worker/  # AWS Batch job definition for Whisper
```

### 1.7 Cost Profile (current, gross before credits)

| Item | Monthly cost |
|---|---|
| 11 NLBs (internal) | ~$198/mo ($18 each) |
| 19 KMS CMKs | ~$19/mo |
| RDS t4g.micro | ~$13/mo |
| ECS Fargate (11 services, 1 task each) | ~$30/mo |
| S3 + CloudFront | ~$5/mo |
| CloudWatch Logs | ~$8/mo |
| ECR | ~$3/mo |
| API GW | ~$2/mo |
| Step Functions, Batch, SES | ~$2/mo |
| **Total gross** | ~$280/mo |
| AWS Activate Founders credits | -$1,000 remaining |
| **Net cost** | ~$0 |

**Budget alert note:** AWS Budget currently alerts on gross Usage charges, not net-of-credits. This caused the false alarm (gross $51.29 with $0 net). Fix: update the budget in the AWS console to filter by `RECORD_TYPE != Credit` or track by net cost. Phil to update manually.

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

**Review checkpoint:** COMPLETED. See session summary. All items shipped.

| Task | PR | Status |
|---|---|---|
| Move ECS to public subnets, remove NAT GW | #346 | MERGED |
| Destroy VPC Interface Endpoints (16 + SG) | #347 | MERGED |
| Destroy WAF (regional + global) | #347 | MERGED |
| Destroy Aurora auth-db | #347 | MERGED |
| Clean dead Terraform refs (backup + ecs) | #348 | MERGED |
| Extend local dev stack (LocalStack + init) | #349 | OPEN/auto-merge |
| Document architecture + service contracts | this PR | IN PROGRESS |

**Wave 0 reconfirmation result:** All AWS resources verified destroyed. ECS services confirmed healthy (public subnet access to ECR, CW, SM). Budget alarm still fires on gross charges (Phil to fix manually -- cannot be automated, requires console). LocalStack now covers secretsmanager, kms, sts, iam, logs for zero-cost local testing.

---

### Wave 0.5: Billing DynamoDB Tables (REQUIRED before Wave 1)

**Status:** IN PROGRESS (PR pending). Blocking issue surfaced during Wave 0 doc work.

**Root cause:** The billing service is deployed and its IAM role is provisioned, but two DynamoDB tables the billing service writes to do not exist in AWS. Any billing route that touches event history or subscription lookup will return `ResourceNotFoundException` at runtime.

**Tables missing:**

| Table | Status | Issue |
|---|---|---|
| `panakoes-dev-billing-events` | Not in Terraform, not in AWS | Billing event log; pk=`USER#<user_id>` (S), sk=`EVENT#<ulid>` (S). The billing service writes every Stripe-processed event here. The IAM forward-ref ARN is correct; the table was never created. An SNS topic with the same name exists in the events module (different AWS namespace, not a conflict). |
| `panakoes-dev-subscriptions` | In Terraform (data/main.tf:330), not in AWS | Current-state view of Stripe subscriptions; pk=`tenant_id` (S), sk=`subscription_id` (S). Never applied to AWS despite being in Terraform. |

**Fix:** add `aws_dynamodb_table.billing_events` to `infra/dev/data/main.tf` and add outputs for both tables to `infra/dev/data/outputs.tf`. The subscriptions table already has a Terraform resource; it will be created on apply.

**Pattern to follow:** The IAM module in `infra/dev/iam/data.tf` already has forward-reference ARNs for both tables at lines 133 and 137. The table names these ARNs encode (`panakoes-dev-billing-events`, `panakoes-dev-subscriptions`) MUST match the Terraform resource `name` fields exactly.

| Task | PR | Status |
|---|---|---|
| Add billing_events DDB table to data module + outputs | #351 (pending) | IN PROGRESS |

**Wave 0.5 review checkpoint:**
- [ ] `aws dynamodb describe-table --table-name panakoes-dev-billing-events` returns ACTIVE
- [ ] `aws dynamodb describe-table --table-name panakoes-dev-subscriptions` returns ACTIVE
- [ ] Billing service CloudWatch logs show no ResourceNotFoundException errors

---

### Wave 1: Zero NLBs (ECS Service Connect + Shared ALB)

**Estimated savings:** $182/mo
**Estimated effort:** 1-2 sessions, 3-4 parallel agents
**Pre-wave review required:** read Wave 0 reconfirmation above. Confirm PR #349 merged and `make test-local` passes on Phil's machine before starting.

**Pre-wave reconfirmation checklist (orchestrator runs before dispatching Wave 1 agents):**
- [ ] `aws elbv2 describe-load-balancers` shows 11 internal NLBs still running
- [ ] `aws ecs list-services --cluster panakoes-dev` shows 11 healthy services
- [ ] `aws apigatewayv2 get-vpc-links` shows VPC Links targeting NLBs
- [ ] `aws servicediscovery list-namespaces` shows no `panakoes-dev.local` namespace (verifies clean slate)
- [ ] `make test-local` passes (validates local stack before any infra change)

**Tasks:**

| Task ID | Description | Files touched | Agent |
|---|---|---|---|
| W1-T1 | Create Cloud Map namespace `panakoes-dev.local` | infra/dev/ecs/main.tf or new infra/dev/service-discovery/main.tf | Agent A |
| W1-T2 | Create shared internal ALB + 11 target groups + listener rules | infra/dev/alb/ (new module) | Agent B |
| W1-T3 | Update API GW VPC Link to target new ALB | infra/dev/api-gateway/main.tf | Agent C (after W1-T2) |
| W1-T4 | Add Service Connect config to all 11 ECS services | infra/dev/ecs/*.tf | Agent D (after W1-T1) |
| W1-T5 | Remove 11 NLBs (terraform destroy NLB resources, git rm if in separate module) | infra/dev/ecs/*.tf | Agent E (after W1-T3 + W1-T4 verified) |
| W1-T6 | Verify all routes through ALB end-to-end | curl tests + CloudWatch metrics | Orchestrator |
| W1-T7 | Update `docs/service-contracts.md` with Service Connect DNS names | docs/service-contracts.md | Agent F |

**Wave 1 review checkpoint (after all tasks complete):**
- [ ] All 11 NLBs confirmed absent in `aws elbv2 describe-load-balancers`
- [ ] ALB listener rules confirmed: `aws elbv2 describe-rules --listener-arn <arn>`
- [ ] Service Connect working: each service can resolve sibling by DNS (`<svc>.panakoes-dev.local`)
- [ ] API GW routes return HTTP 200 via curl (stage-prefixed URL)
- [ ] No 5xx spike in CloudWatch for 5 minutes after cutover
- [ ] `make test-local` still passes (no regression)
- [ ] Monthly cost recalculation: $280 - $182 = ~$98/mo gross

**Wave 1 critical agent constraints:**
- Agent D must NOT remove NLB Terraform resources (only adds Service Connect). NLB removal is Wave 1-T5.
- Agent E (NLB removal) must only run AFTER Agent C (API GW updated) has applied AND routes have been verified via curl.
- Agents A and B can run in parallel. Agent C blocks on B. Agent D blocks on A. Agent E blocks on C+D.

---

### Wave 2: KMS Consolidation

**Estimated savings:** $15/mo
**Estimated effort:** 0.5-1 session, 2 agents
**Pre-wave review required:** Wave 1 review checkpoint completed and signed off.

**Pre-wave reconfirmation checklist:**
- [ ] All 19 KMS aliases listed under `aws kms list-aliases --query 'Aliases[?starts_with...]'`
- [ ] No service errors in CloudWatch for past 24h (healthy baseline before key rotation)
- [ ] `docs/service-contracts.md` is current (agent needs accurate KMS alias names)

**Tasks:**

| Task ID | Description | Files touched | Agent |
|---|---|---|---|
| W2-T1 | Create new CMKs: `panakoes/app-data` + `panakoes/logs` | infra/dev/iam/main.tf or new infra/dev/kms/main.tf | Agent A |
| W2-T2 | Update S3 bucket encryption configs to `panakoes/app-data` | infra/dev/storage/main.tf, infra/dev/frontend/main.tf | Agent B (after W2-T1 applied) |
| W2-T3 | Update Secrets Manager, SQS, SNS, ECR to `panakoes/app-data` | infra/dev/secrets/main.tf, infra/dev/events/main.tf, infra/dev/ecr/main.tf | Agent B (same agent, same PR) |
| W2-T4 | Update CloudWatch Logs KMS refs to `panakoes/logs` | infra/dev/observability/main.tf, infra/dev/api-gateway/main.tf, relevant log group refs | Agent C (after W2-T1 applied) |
| W2-T5 | Update RDS to use `panakoes/app-data` | infra/dev/auth-db-rds/main.tf | Agent B (same PR, add to W2-T2/T3) |
| W2-T6 | Update ECS task definitions to remove per-service CMK refs | infra/dev/ecs/*.tf | Agent D (after W2-T1 applied) |
| W2-T7 | Schedule old 15 CMKs for deletion (7-day window via AWS CLI) | n/a -- CLI only, no Terraform | Orchestrator (not an agent -- destructive) |
| W2-T8 | Verify no errors 24h after key rotation | CloudWatch alarm review | Orchestrator |

**Wave 2 review checkpoint:**
- [ ] `aws kms list-aliases` shows only 4 panakoes CMKs
- [ ] `aws kms list-keys` + `aws kms describe-key` confirms old keys in `PendingDeletion` state
- [ ] S3 bucket encryption: `aws s3api get-bucket-encryption` returns new key ARN
- [ ] Secrets Manager: `aws secretsmanager describe-secret --secret-id <name>` shows new key ARN
- [ ] RDS: `aws rds describe-db-instances` shows new KMS key
- [ ] No CloudWatch alarm for 24h post-rotation
- [ ] Monthly cost: ~$98 - $15 = ~$83/mo gross

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
