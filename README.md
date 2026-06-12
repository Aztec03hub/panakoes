# Panakoes

> All-hearing audio capture, transcription, and insights. The open-source cloud platform powering the LaFayette Labs wearable and any other audio source.

**Live admin dashboard:** [`https://admin.panakoes.com`](https://admin.panakoes.com)  &middot;  **Public API:** [`https://api.panakoes.com`](https://api.panakoes.com/v1/auth/health)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Status](https://img.shields.io/badge/status-pre--alpha%20(dev%20live)-orange)
![Region](https://img.shields.io/badge/AWS-us--east--1-success)
[![Python tests](https://github.com/Aztec03hub/panakoes/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/pytest.yml)
[![TypeScript tests](https://github.com/Aztec03hub/panakoes/actions/workflows/vitest.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/vitest.yml)
[![Terraform CI](https://github.com/Aztec03hub/panakoes/actions/workflows/terraform-ci.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/terraform-ci.yml)
[![CodeQL](https://github.com/Aztec03hub/panakoes/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/codeql.yml)
[![License check](https://github.com/Aztec03hub/panakoes/actions/workflows/license-check.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/license-check.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Aztec03hub/panakoes/badge)](https://scorecard.dev/viewer/?uri=github.com/Aztec03hub/panakoes)

---

## What this is

Panakoes ingests audio (uploaded files or live wearable streams), transcribes it with GPU-accelerated speech recognition, and produces AI-powered summaries and action items. It is built on AWS, fully infrastructure-as-code, designed for production observability and audit from day one, and engineered to swap models as the state of the art moves.

Audience-wise it is both the cloud backend for an upcoming AI wearable (a Plaud Note Pro competitor, forking [Omi](https://github.com/BasedHardware/Omi)) and a public reference for how a single operator builds a production-grade AWS platform with modern tooling. The name is constructed Greek for "all-hearing", a parallel to Argus Panoptes ("all-seeing").

The project is the first open-source initiative under [LaFayette Labs LLC](https://lafayettelabs.com).

## Status today (2026-06-12)

This is a pre-alpha personal project running in a dev AWS account. It is not yet production-ready and is not accepting external traffic. What works right now:

- The admin SPA is live at `https://admin.panakoes.com` (SvelteKit on S3 + CloudFront, ACM-issued multi-SAN cert, Cloudflare DNS).
- The public API is live at `https://api.panakoes.com` (API Gateway v2 HTTP API, custom domain, regional endpoint, scoped CORS).
- The auth service (Better-Auth + Hono + Drizzle, JWT HS256 with an RS256-via-KMS path) is up and signing tokens.
- The admin-api, cost-api, notification, and session-manager services are running on ECS Fargate Spot.
- The full async transcription path (S3 upload event, Step Functions fan-out, AWS Batch GPU, Whisper-large-v3, S3 transcript, DynamoDB, Claude summary) is implemented; the streaming path (API Gateway WebSocket, session-spawned g4dn.xlarge Spot GPU, faster-whisper-large with Silero VAD) is implemented.
- Wave 1 cost-reduction pass shipped: Container Insights disabled, NAT removed in favor of public-subnet ECS, ALB consolidation, Fargate Spot, 7-day CloudWatch retention. Wave 2 KMS migration (17 per-service CMKs to 2 consolidated CMKs) applied. Steady-state monthly gross is roughly $55 covered by AWS Activate Founders credits.
- 431+ PRs merged on the default branch, 25 Architecture Decision Records, 28 Terraform modules, 28 services or shared libraries.

See [`docs/STATUS.md`](docs/STATUS.md) for the always-current "where we are right now" snapshot, [`CHANGELOG.md`](CHANGELOG.md) for the release log, and [`docs/cost-analysis-2026-05-19.md`](docs/cost-analysis-2026-05-19.md) for the live cost breakdown.

## What this demonstrates

This repository is a working portfolio piece for AWS Solutions Architecture and modern platform engineering. Concretely:

- **Polyglot microservices on AWS Fargate.** Python (FastAPI) and TypeScript (Hono) services on ECS Fargate Spot, fronted by an HTTP API and an internal ALB, networked via ECS Service Connect.
- **Infrastructure as code.** 28 Terraform modules covering VPC, IAM, KMS, ECS, ALB, API Gateway, RDS, DynamoDB, S3, CloudFront, AWS Batch, Step Functions, Lambda, ECR, SES, WAF, observability, backups, and cost monitoring. State in S3 with KMS encryption and S3-native locking. CI plans every module on every PR.
- **Identity and security.** Better-Auth-backed JWT issuance, with an RS256 signing path that holds the private key inside AWS KMS and exposes a live JWKS endpoint at `/v1/auth/.well-known/jwks.json` (ADR-041). MFA-aware step-up flows. Least-privilege IAM at a per-service granularity, no wildcards on resource ARNs.
- **Audit and observability.** Application-level audit log in DynamoDB plus AWS-API-level CloudTrail. OpenTelemetry instrumentation in every service through the AWS Distro for OpenTelemetry, fanning out to CloudWatch and AWS X-Ray. CloudWatch Logs roll into S3 and are queryable in Athena.
- **Cost discipline.** Single-operator dev environment runs about $55 per month gross, fully absorbed by an AWS Activate Founders credit grant. Cost analysis is checked into the repo; every line item has been drilled and the deferred levers are documented (IPv6 Fargate, ALB-to-API-Gateway swap, Aurora Serverless v2 scale-to-zero).
- **Engineering discipline.** Conventional Commits, per-PR changelog fragments, branch protection with linear history, signed-commit gating, secret scanning on every push (gitleaks pre-commit, GitHub secret push protection, CodeQL, Trivy), em-dash rejection, IAM-wildcard rejection, JWT-prefix rejection, and a 24-check CI gate on every PR.
- **AI-augmented development as a first-class workflow.** The repository encodes a documented orchestrator-delegation pattern (ADR-024) with parallel worktrees (ADR-021), structured agent run reports (ADR-025), file-defined long-running agents (ADR-045), and local-first verification discipline (ADR-046).

## Architecture

A detailed write-up lives in [`docs/architecture.md`](docs/architecture.md), and architectural decisions with rationale live as Architecture Decision Records in [`docs/adr/`](docs/adr/).

**High-level shape:** event-driven microservices on AWS.

- **Async transcription path.** Client uploads an audio file via a pre-signed S3 PUT (issued by `ingestion-api`). The S3 PutObject event flows through EventBridge into SQS, where `transcribe-worker` picks it up. For audio longer than ten minutes, a Step Functions state machine fans out chunks to AWS Batch on EC2 g4dn.xlarge Spot GPUs running Whisper-large-v3 fp16. Transcripts land in S3 and stream through DynamoDB into the `summarization` service, which calls Claude Haiku (default) or Claude Sonnet (paid-tier deep summary). Final summary plus action items returns through the `query-api`.
- **Streaming transcription path.** Client opens an API Gateway v2 WebSocket. `session-manager` and a Lambda-driven `gpu-spawner` provision a per-session g4dn.xlarge Spot GPU running `faster-whisper-large` with Silero VAD. Partial transcripts stream back over the same WebSocket sub-second once the instance is warm. ADR-037 documents the pluggable Transcriber abstraction; backend swaps require no service changes.
- **Admin dashboard.** SvelteKit static bundle on S3 behind CloudFront at `admin.panakoes.com` (with a multi-SAN ACM certificate covering admin, api, www, and the apex of panakoes.com). The SPA calls the public HTTP API at `api.panakoes.com`. The dashboard surfaces live ECS health (via `health-aggregator`), per-service cost rollups (via `cost-api` and the nightly `cost-rollup-aggregator` Lambda, ADR-040), and lifecycle controls for the underlying ECS services.

```
   ┌────────────┐                  ┌─────────────┐
   │ Wearable / │                  │  Admin SPA  │
   │ Web client │                  │ (SvelteKit) │
   └─────┬──────┘                  └──────┬──────┘
         │ wss://                         │ https://
         │ api.panakoes.com               │ admin.panakoes.com
         ▼                                ▼
   ┌────────────────────────────┐    ┌────────────┐
   │  API Gateway v2 (HTTP/WS)  │    │ CloudFront │
   └─────┬──────────────────────┘    └──────┬─────┘
         │ /v1/<service>/{proxy+}           │
         ▼                                  ▼
   ┌──────────────────────────┐        ┌──────────┐
   │ ECS Fargate Spot         │        │ S3 (SPA) │
   │  auth, admin-api, ...    │        └──────────┘
   └──────────────────────────┘
         │
         ▼
   RDS Postgres, DynamoDB, S3 (audio + transcripts)
   AWS Batch GPU, Step Functions, EventBridge, SQS, SNS, SES
   CloudWatch, X-Ray, ADOT, CloudTrail, Athena
```

## Tech stack and trade-offs

| Layer | Choice | Why |
|---|---|---|
| Cloud | AWS, us-east-1 | Deep service coverage and cheapest network egress. Region pinned in every module. |
| IaC | Terraform 1.10 + S3 + KMS state backend | Declarative, multi-cloud-portable, state encrypted at rest. |
| Languages | Python 3.12 (services) + TypeScript on Bun (auth) | Right tool per service; FastAPI and Pydantic for data services, Hono for the auth edge. |
| Frontend | SvelteKit on S3 + CloudFront | Smallest bundle for the same surface, AWS-native hosting, no Vercel dependency. |
| Auth | Better-Auth + Drizzle ORM, HS256 default with RS256-via-KMS opt-in | Modern session and MFA primitives; private key lives in KMS, never on the container (ADR-041). |
| Async transcription | AWS Batch + EC2 g4dn.xlarge Spot + Whisper-large-v3 fp16 | Pennies per audio hour at SOTA accuracy. |
| Streaming transcription | Session-spawned g4dn.xlarge Spot + faster-whisper-large + Silero VAD | Sub-second latency once warm. |
| AI summarization | Claude Haiku 4.5 default, Claude Sonnet 4.6 on paid tier | Cost discipline plus a clean paid-tier differentiator. |
| Payments | Stripe | Real billing experience, industry-standard webhooks. |
| Observability | CloudWatch + AWS X-Ray (OpenTelemetry via ADOT) | AWS-native backends, vendor-neutral instrumentation. |
| Logging | CloudWatch Logs 7-day retention, S3 archive, Athena | Cheap to ingest, queryable at any timescale. |
| Audit trail | DynamoDB app-level log + CloudTrail | Application semantics plus AWS-API semantics. |
| Testing | pytest + pytest-asyncio + httpx + testcontainers + moto (Python); vitest + msw (TS); Playwright (e2e) | TDD discipline with real Postgres in integration tests, not mocks. |
| CI | GitHub Actions, OIDC federation to AWS | No long-lived AWS access keys anywhere. |
| Secrets | AWS Secrets Manager (runtime), SSM Parameter Store (config) | Public repo means no secrets in source code, ever. |

## Engineering principles

These are encoded in [`CLAUDE.md`](CLAUDE.md) (project conventions), [`WORKFLOW.md`](docs/dev/WORKFLOW.md) (day-to-day rhythms), and the ADR set.

1. **Conventional Commits for every commit.** Squash-merge to a linear `main`. No force-push to `main`. No `--no-verify` except documented emergency.
2. **Per-PR changelog fragments (ADR-026).** Every PR drops one `.changelog/<UTC>-<slug>.md` file. The release script (`scripts/assemble-changelog.sh`) rolls them up at tag time. Eliminates the DIRTY-PR cascade that monolithic CHANGELOG editing causes.
3. **TDD where it matters.** Coverage gates in CI: 80 percent on application services, 100 percent on auth and billing and audit paths, 70 percent on infra-adjacent code.
4. **No secrets in source code.** Gitleaks pre-commit, GitHub secret push protection, OIDC for AWS, no long-lived keys. `.gitignore` blocks `.env`, `.tfstate`, key files.
5. **Least-privilege IAM.** Every task role and Lambda role is scoped to a specific resource ARN. The `panakoes-iam-star` CI scanner blocks wildcard resources unless the same line carries a `# panakoes-iam-policy-resource-star: justified` annotation explaining why.
6. **Local-first verification (ADR-046).** Run `make ci-fast` before pushing. The pre-push hook enforces this. Skipping it has cost us cycles often enough that we wrote the discipline down.
7. **No em-dashes.** Hard rule across the codebase, enforced by the `panakoes-em-dash` scanner.
8. **AI-augmented development is structured.** The orchestrator-delegation pattern (ADR-024), parallel git worktrees (ADR-021), structured agent run reports under `.agent-runs/` (ADR-025), and file-defined long-running agents under `.claude/agents/` (ADR-045) make AI sub-agents observable, auditable, and reproducible.

## Project layout

```
panakoes/
├── services/             28 microservices and shared libraries (Python + TypeScript)
├── infra/                28 Terraform modules across dev environment + global config
│   └── dev/              per-module Terraform (ecs, alb, api-gateway, frontend, ...)
├── web/                  SvelteKit admin dashboard source
├── tests/                cross-service integration and e2e tests
├── scripts/              dev-up, tf wrappers, telemetry, branch hygiene, dependency-update helpers
├── docs/
│   ├── adr/              25 Architecture Decision Records (ADR-021 onward)
│   ├── architecture.md   detailed system architecture
│   ├── runbooks/         operator runbooks
│   ├── operator/         "what's waiting on a human" board
│   └── STATUS.md         live state of deployed services and open backlog
└── .github/              CI workflows, issue + PR templates, custom scanners
```

## Quick start (local development)

Clone, bootstrap, and arm the pre-push hook:

```bash
git clone https://github.com/Aztec03hub/panakoes.git
cd panakoes
make setup           # install Python dev deps for every service
make install-hooks   # arm the pre-push hook (runs `make ci-pr` before every push)
```

`make install-hooks` is idempotent. It points `core.hooksPath` at the version-controlled `.githooks/` directory so the pre-push gate applies automatically.

Two paths for the data services, pick whichever matches your setup.

**1. Native (WSL2, Linux, macOS) with Docker installed:**

```bash
make dev-up                          # postgres + dynamodb-local
DEV_LOCALSTACK=1 make dev-up         # also start localstack (S3, EventBridge, SNS, SQS)
make dev-down                        # stop the stack
```

`make dev-up` prints the `DATABASE_URL`, `DDB_ENDPOINT_URL`, and `AWS_ENDPOINT_URL` values your services expect.

**2. VS Code Codespaces / Dev Containers:**

Open the repo in a Codespace or with the VS Code Dev Containers extension. The [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json) config provisions Python 3.12, Node 22, Terraform 1.10, AWS CLI, and Docker-in-Docker; its `postCreateCommand` installs `uv`, `pnpm@11.0.8`, `gitleaks`, `pre-commit`, and `packer`. Inside the container, run `make dev-up` to start the data services.

Per-service developer notes live in `services/<name>/README.md`. Cross-service docs live in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Documentation map

Start here:

- [`docs/STATUS.md`](docs/STATUS.md) for the live "what is deployed right now" snapshot.
- [`PLANNING.md`](PLANNING.md) for architectural decisions and the evolution log.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) for a curated "what's built / what's next" summary.
- [`docs/dev/SCOPE.md`](docs/dev/SCOPE.md) for the detailed MVP scope decision log and phase-2 backlog.
- [`docs/architecture.md`](docs/architecture.md) for the detailed system write-up.
- [`docs/adr/`](docs/adr/) for the full Architecture Decision Record catalog.
- [`docs/cost-analysis-2026-05-19.md`](docs/cost-analysis-2026-05-19.md) for the live AWS cost breakdown with per-line-item drill-downs.
- [`docs/operator/aws-cloudflare-actions.md`](docs/operator/aws-cloudflare-actions.md) for the operator runbook (the "waiting on a human" board).
- [`docs/runbooks/`](docs/runbooks/) for disaster recovery, incident response, and dev troubleshooting.
- [`CHANGELOG.md`](CHANGELOG.md) for the release log.
- [`SECURITY.md`](SECURITY.md) for the security and disclosure policy.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) for contributor onboarding.
- [`CLAUDE.md`](CLAUDE.md) and [`WORKFLOW.md`](docs/dev/WORKFLOW.md) for Claude Code conventions and day-to-day rhythms.

## License

MIT. See [`LICENSE`](LICENSE).

## About LaFayette Labs

Panakoes is built by [LaFayette Labs LLC](https://lafayettelabs.com), an Illinois-registered hardware and software lab focused on AI-augmented sensor and audio systems. Founded by Phil LaFayette ([@Aztec03hub](https://github.com/Aztec03hub) on GitHub).

If you are hiring for AWS Solutions Architecture, platform engineering, or applied-AI roles and want to chat about the engineering decisions in this repo: `phil@lafayettelabs.com`.
