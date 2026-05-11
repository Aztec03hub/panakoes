# Project Status

**Last updated:** 2026-05-09 (during the dev-environment apply marathon).

This document is the "where we are right now" snapshot. A fresh contributor (or fresh Claude instance) cloning the repo should read this AFTER `README.md` + `CLAUDE.md` + `PLANNING.md` + `SCOPE.md`, and BEFORE picking up work. It captures live state that is not derivable from `git log`, the code, or the operator runbook on its own.

## How to keep this current

Update STATUS.md in the same PR that materially changes the deployed environment, the open-work backlog, or a known partial / broken state. Do not let it drift; a stale STATUS.md is worse than no STATUS.md because it lies.

When in doubt, prefer **deletion** of stale items over speculative updates. The truth is "this is what we know is true today"; speculation belongs in `PLANNING.md` or an issue tracker.

---

## 1. Repo + environment

- **Repo:** `Aztec03hub/panakoes` on GitHub (Phil's personal account; mirror to a future LaFayette Labs org is possible but not done).
- **License:** MIT.
- **Default branch:** `main`. Linear history (squash-merge only). No force-push to main.
- **Branch protection:** PR-required + status-checks + linear-history; admin bypass actor configured per ADR-030 for emergencies only.
- **Auto-merge:** standard flow is `gh pr merge --squash --auto --delete-branch` (workflow detail in `feedback_panakoes_lessons.md` memory; not repeated here).
- **AWS account:** `659225405128`. IAM admin user `phil`. Local CLI profile `panakoes-admin`. Region `us-east-1`.
- **Domains (Cloudflare-registered):** `panakoes.com` (project), `lafayettelabs.com` (LLC).
- **AWS Activate Founders:** approved 2026-05-09 after re-submission with `phil@lafayettelabs.com` root email (Cloudflare Email Routing forwards to `plafaydev@gmail.com`).

## 2. Operator runbook pointer

The `docs/operator/aws-cloudflare-actions.md` document is the **source of truth for "what's waiting on Phil"** (manual AWS Console clicks, Cloudflare DNS records, secret population). It tracks each action with `[ ]` / `[~]` / `[x]` indicators.

STATUS.md (this file) is the source of truth for **architectural state**: which services are coded, which infra modules are deployed, which background work is mid-flight. The two files are deliberately separated. When in doubt, the operator runbook is for "what to type into a console"; STATUS.md is for "what's the system actually like right now."

## 3. Services (microservices in `services/`)

| Service | Code status | Image in ECR | Deployed to ECS | Notes |
|---|---|---|---|---|
| `services/_template` | Reference template | n/a | n/a | Skeleton every new Python service copies. |
| `services/audit-lib` | Shipped | n/a (lib) | n/a | DynamoDB + Memory + Stdout backends, 100% coverage. ADR-023. |
| `services/auth` | Shipped | No | No | TS + Hono + Better-Auth, HS256 JWT (slice 1), Aurora-backed. ADR-022. |
| `services/auth-client` | Shipped | n/a (lib) | n/a | Python JWT verifier consumed by every Python service. |
| `services/middleware-lib` | Shipped | n/a (lib) | n/a | Rate-limit + body-size + request-id middleware. |
| `services/models-lib` | Shipped | n/a (lib) | n/a | Shared Pydantic models. |
| `services/test-helpers` | Shipped | n/a (lib) | n/a | jwt + aws + factories. |
| `services/otel-lib` | Shipped | n/a (lib) | n/a | OTEL setup for Python services. |
| `services/otel-lib-ts` | Shipped | n/a (lib) | n/a | OTEL setup for TS services. |
| `services/ingestion-api` | Shipped | No | No | FastAPI, S3 pre-signed PUT issuer + DynamoDB ingestion record. Transcription wired via the `Transcriber` abstraction (env-var-selectable backend, Groq default; on-demand `POST /api/v1/transcribe/{id}` route still works for manual retries). Auto-trigger via S3 -> EventBridge -> SQS -> `services/transcribe-worker` is now the primary path. |
| `services/query-api` | Shipped | No | No | FastAPI, transcript / summary read API. |
| `services/summarization` | Shipped | No | No | FastAPI, calls Anthropic (Haiku 4.5 default). |
| `services/notification` | Shipped | No | No | FastAPI, SNS dispatch. |
| `services/session-manager` | Shipped | No | No | FastAPI, streaming session lifecycle. |
| `services/billing` | Shipped | No | No | Stripe webhook handler (TEST mode), idempotency on event id. |
| `services/event-router` | Shipped | No | No | Lambda container image, EventBridge → SNS routing. |
| `services/transcribe-worker` | Shipped | No | No | Lambda container image, SQS-driven auto-transcription consumer. Re-uses ingestion-api's `transcribe_ingestion` orchestration. Provisioned via `infra/dev/transcribe-worker`. |
| `services/gpu-spawner` | Shipped | No | No | Lambda, EC2 RunInstances for streaming sessions. |
| `services/cost-api` | Shipped | No | No | Tier 2 admin: by-service / by-tenant / anomalies cost views, DynamoDB read-through cache (ADR-031). Anomalies route returns `[]` until `infra/dev/cost-anomaly-monitor` is applied. By-tenant route returns empty rows until `services/cost-rollup-aggregator` ships data into the rollup table. |
| `services/cost-rollup-aggregator` | Shipped | No (Lambda; needs first push) | n/a (Lambda, not ECS) | Container-image Lambda fired nightly at 02:00 UTC by EventBridge Scheduler. Populates `panakoes-dev-tenant-cost-rollup` from AWS Cost Explorer per-tenant data; this is the producer side of the by-tenant route in cost-api. Reuses cost-api's `TenantRollupStore` via path-dep. Until the per-tenant tagging policy lands, all spend bucket-up under the synthetic tenant id `__untagged__`. |
| `services/admin-api` | Shipped | No | No | Tier 3 admin: 8 lifecycle ops (Phase 1 + Phase 2) + audit-log read. Safety pattern per ADR-032; response semantics per ADR-033. |
| `services/admin` | Shipped (frontend) | n/a (S3 origin) | n/a | SvelteKit admin dashboard. Tiers 1, 2, 3 frontend pages all wired. Built but not yet deployed to the S3 origin bucket. |

**Container deployment:** every service that ships an image is **coded but unbuilt + unpushed + undeployed**. Per `docs/operator/aws-cloudflare-actions.md` Section E, the next operator-side step for any service is `docker build → docker push → ECS task definition`. ECS task definitions are not yet authored (no `infra/dev/<service>/` modules exist for the application services; only the platform modules in `infra/dev/`).

## 4. Infrastructure (Terraform modules in `infra/`)

Status per module as of 2026-05-09 evening:

| # | Module | Code | Apply state | Notes |
|---|---|---|---|---|
| 1 | `infra/bootstrap` | Shipped | Applied | State backend: bucket `panakoes-tf-state-b291597a`, KMS key `dce57db1-ea8c-46dd-b60a-c8de022860af`. State locking via S3 conditional writes (`use_lockfile = true`); the legacy `panakoes-tf-lock` DynamoDB table was retired 2026-05-09 (issue #153). |
| 2 | `infra/global` | Shipped | Applied | OIDC provider for `Aztec03hub/panakoes`, GitHub Actions assume-role. |
| 3 | `infra/dev/network` | Shipped | Applied | VPC `10.0.0.0/16`, 3 AZ, public + private + isolated subnets, single-AZ NAT Gateway (intentional dev cost choice). |
| 4 | `infra/dev/data` | Shipped | Applied | DynamoDB: `ingestion`, `audit-log`, `streaming-sessions`, all PAY_PER_REQUEST. |
| 5 | `infra/dev/admin-state` | Shipped | Applied | DynamoDB: `cost-cache`, `tenant-cost-rollup`, `lifecycle-state`, `alert-state`. |
| 6 | `infra/dev/storage` | Shipped | Applied | S3: `audio-uploads`, `transcripts`, `log-archive`. CMK-encrypted. |
| 7 | `infra/dev/secrets` | Shipped | Applied | 7 secret resources, ALL still placeholder values. See section 5 below. |
| 8 | `infra/dev/ecr` | Shipped | Applied | 13 ECR repos (was 11 before PR #161 added `cost-api` + `admin-api`). This PR adds a 14th: `cost-rollup-aggregator`. Re-apply on merge. |
| 9 | `infra/dev/iam` | Shipped | Applied | Per-service task + execution roles, least-privilege. |
| 10 | `infra/dev/observability` | Shipped | Applied | CloudWatch log groups + S3 archive lifecycle. PR #161 added `cost-api` + `admin-api` log groups. |
| 11 | `infra/dev/events` | Shipped | Applied | EventBridge custom bus + SNS topic + SQS queues. |
| 12 | `infra/dev/security` | Shipped | **Not applied** | GuardDuty + Config + Security Hub. ~$8-10/mo; deferred. |
| 13 | `infra/dev/auth-db` | Shipped | **Not applied** | Aurora Serverless v2. Verify auto-pause before applying. |
| 14 | `infra/dev/vpc-endpoints` | Shipped | **Not applied** | Verify endpoint count before applying ($0.01/hour each). |
| 15 | `infra/dev/waf` | Shipped | Applied | WAFv2 regional ACL + 4 AWS managed rules + per-IP rate limit + KMS-encrypted log group. |
| 16 | `infra/dev/backup` | Shipped | Applied 2026-05-09 | AWS Backup vault `panakoes-dev`, plan id `dc833f71-0403-4983-aa58-7c3cb4b6cc41`, dedicated CMK alias backed by key `b8f560b8-bd58-4194-9207-342a0d6085e2`, service role `panakoes-dev-backup`. 9 resources. |
| 17 | `infra/dev/api-gateway` | Shipped | **PARTIAL** | API + VPC link + KMS + log group landed; 9 service integrations + WAF association FAILED on first apply (services-with-NLBs do not exist yet). Decision: leave partial state in place (~$1/mo), revisit when first ECS service with NLB lands. Memory: `aws_api_gateway_partial_apply.md`. Backlog: task #109. |
| 18 | `infra/dev/step-functions` | Shipped | **Not applied** | Long-audio chunking workflow. |
| 19 | `infra/dev/batch` | Shipped | **Not applied** | AWS Batch GPU compute environment. |
| 20 | `infra/dev/frontend` | Shipped | Applied 2026-05-09 (after PR #160 v2 logs fix) | CloudFront distribution `E42AJI7SB5K1N` at `dmaopcm3hnxog.cloudfront.net`. Origin bucket `panakoes-dev-frontend-9d80ace6`, log bucket `panakoes-dev-frontend-logs-ef03950e`. CMK alias `alias/panakoes-dev-frontend`. CloudFront access logs use v2 (CWL Delivery → S3) per ADR-034. |
| 21 | `infra/dev/cost-anomaly-monitor` | Shipped (PR #163, in flight) | **Not applied** | Cost Anomaly Monitor (DIMENSIONAL/SERVICE) + IMMEDIATE EMAIL subscription. After apply, Phil must confirm the SNS subscription email. Unblocks the cost-api `/cost/anomalies` route. |
| 22 | `infra/dev/transcribe-worker` | Shipped | **Not applied** | SQS trigger queue + DLQ + dedicated CMK + EventBridge rule on the default bus + S3 EventBridge notification on the audio-uploads bucket + Lambda function (container image) + IAM runtime policy + log group + DLQ alarm. After apply: build + push the worker container image to ECR, populate `panakoes-dev/groq-api-key`, then update Lambda env to inject the key. |
| 23 | `infra/dev/cost-rollup-aggregator` | Shipped | **Not applied** | Lambda (`panakoes-dev-cost-rollup-aggregator`, container image, 256 MB, 5-minute timeout, reserved concurrency 1) + EventBridge Scheduler rule (`panakoes-dev-cost-rollup-nightly`, 02:00 UTC daily) + IAM (least-privilege CE read + DDB PutItem on rollup table only) + KMS-encrypted log group (30-day retention). First-apply order: re-apply `infra/dev/ecr` to provision the new repo, `docker build && docker push :latest` from the repo root, then `terraform apply` here. Lambda validates `image_uri` at create time so the image must exist first. Unblocks the cost-api `/cost/by-tenant` route. |

**Estimated steady-state dev cost** of currently-applied modules: ~$33-38/mo (NAT gateway dominates at ~$32, all DynamoDB + S3 + CloudFront + WAF essentially free at this volume).

## 5. AWS Secrets Manager state

All 7 `panakoes-dev/*` secrets exist as Terraform-provisioned **placeholders** (per `lifecycle.ignore_changes = [secret_string]`). Real values must be written via AWS CLI before each consumer service can boot. Detailed table in operator guide Section D and memory `aws_secrets_panakoes_dev.md`. None are populated.

Highest-priority to populate first: `panakoes-dev/jwt-signing-secret` (every JWT-validating service depends on it).

## 6. Open work / backlog (as of 2026-05-09)

### Application

<<<<<<< HEAD
- **Tier 2 Phase 2.2:** nightly cost rollup aggregator job (writes to `tenant-cost-rollup` DynamoDB table). Without it, the by-tenant route returns empty rows.
- **Tier 2 Phase 2.2:** SHIPPED in `services/cost-rollup-aggregator/` + `infra/dev/cost-rollup-aggregator/`. Operator follow-up to land it in dev: (1) re-apply `infra/dev/ecr` for the new repo, (2) `docker push :latest`, (3) `terraform apply infra/dev/cost-rollup-aggregator`. Per-tenant tagging policy (so spend decomposes by tenant rather than landing in `__untagged__`) is a separate piece of work and remains TODO.
- **Tier 3 Phase 2:** DONE. All 8 lifecycle ops shipped (block-tenant, revoke-api-key, kill-streaming-session, kill-batch-job, force-billing-recompute landed alongside the Phase 1 trio). Operator follow-ups required: (1) provision `panakoes-dev-tenants` and `panakoes-dev-api-keys` DynamoDB tables in `infra/dev/data/`; (2) extend admin-api task role with `events:PutEvents` on the panakoes-dev events bus, `batch:TerminateJob` on `arn:aws:batch:us-east-1:*:job/*`, and `dynamodb:UpdateItem`/`GetItem` on the new tables.
- ~~**Transcription auto-trigger:**~~ DONE. `services/transcribe-worker` + `infra/dev/transcribe-worker` ship the S3 ObjectCreated -> EventBridge -> SQS -> Lambda pipeline that fans every audio upload into `transcribe_ingestion()`. Operator follow-up: apply the new module, build + push the worker container image to ECR, populate `panakoes-dev/groq-api-key` in Secrets Manager, and inject the key into the Lambda env. The on-demand `POST /api/v1/transcribe/{id}` route still works (manual / front-end-driven retries).

### Infrastructure

- Apply: `auth-db`, `vpc-endpoints`, `step-functions`, `batch` (deferred until needed; cost-conscious).
- Apply: `cost-anomaly-monitor` (PR #163, queued).
- Defer: `security`, `backup` (cost-conscious; not strictly needed for dev).
- Fix + re-apply: `api-gateway` (partial state, blocked on first ECS service with NLB).
- ~~Bump `terraform-aws-modules/vpc/aws` when upstream fixes the `data.aws_region.current.name` deprecation (provider 6.x flagged it; not blocking).~~ DONE: bumped `infra/dev/network` to `~> 6.0` (resolves the deprecation; v5.21.0 SHA was also intermittently unreachable on GitHub, blocking `terraform init` and `make ci-pr`).
- ~~Retire `aws_dynamodb_table.tf_state_lock`~~ DONE 2026-05-09. Closed in the decommission PR; closes issue #153.

### Container builds + ECS deploys (Section E + beyond)

- Build + push container images for all microservices that have Dockerfiles (none done).
- Author `infra/dev/<service>/` ECS task definition + service modules for each microservice (none exist yet).
- Land first end-to-end service deploy (likely `auth`), then re-apply `api-gateway` to land its first integration.

### Cloudflare DNS (Section F)

- Add `admin.panakoes.com` CNAME → `dmaopcm3hnxog.cloudfront.net`.
- Add `api.panakoes.com` CNAME → API Gateway endpoint (once that module is in a clean-applied state).
- Configure Stripe webhook endpoint pointing at the deployed billing service URL.

## 7. Known partial / broken / deferred state

- **`infra/dev/api-gateway` partial-applied.** Memory: `aws_api_gateway_partial_apply.md`. Do NOT `terraform destroy`; do NOT manually delete via console.
- **`auto-update-prs` PAT** expires ~2026-08-06. Operator guide Section H walks rotation.
- **GitHub `dependabot.yml` secret** (separate from Actions secrets) needs the same PAT for grouped Dependabot rebases.

## 8. Recently shipped (last few sessions)

For commit-level history use `git log --oneline --since='2 weeks ago'`. The high-level shipped slices since the start of the dev-environment apply marathon (2026-05-08 through 2026-05-09):

- Operator-actions guide authored (PR #150).
- `scripts/tf.sh` apply-walkthrough helper (PR #158).
- WAF web ACL apply unblocked (PR #159: parens in description).
- Frontend CloudFront access logs migrated v1 → v2 (PR #160; ADR-034).
- `cost-api` + `admin-api` registered in ECR + observability (PR #161).
- All Terraform S3 backends migrated to `use_lockfile = true` (PR #162).
- Cost Anomaly Monitor module added (PR #163; pending apply).
- Tier 2 Phase 2.1 (by-tenant) + 2.3 (anomalies); Tier 3 Phase 1 (3 lifecycle ops) + Phase 3 (audit log read) - all merged in earlier session waves.

## 9. For the fresh contributor / fresh Claude

Required reading order:

1. `README.md` - public face, what + why.
2. `CLAUDE.md` - discipline rules + working modes (orchestrator-delegation; worktrees for parallel agents).
3. `PLANNING.md` - architectural decisions (ADR-001 through ADR-020 inline; ADR-021+ in `docs/adr/`).
4. `SCOPE.md` - what's in v0.1 vs deferred to phase 2.
5. **This file (`docs/STATUS.md`)** - where we are right now.
6. `docs/operator/aws-cloudflare-actions.md` - what manual operator steps are pending.
7. `docs/architecture.md` - services + data flow + AWS map.
8. `docs/adr/` - every architectural decision since ADR-021.
9. `docs/runbooks/` - DR + incident + dev-troubleshooting.
10. `.agent-runs/README.md` - required format for sub-agent run reports.

Then `gh pr list` + `git log --oneline -20` to see recent activity.

If you are an automated assistant and need a single "what's the current task list" answer, there is intentionally no in-repo task tracker. Tasks live in the operating Claude's session memory. When STATUS.md is current, the work backlog is the union of:

- Section 6 above (the explicit backlog).
- `[ ]` items in `docs/operator/aws-cloudflare-actions.md` (operator-side actions).
- Open issues / open PRs on GitHub (in-flight code work).

If those three sources disagree, **STATUS.md is wrong**. Either the operator guide or the open PRs are authoritative; update STATUS.md immediately.
