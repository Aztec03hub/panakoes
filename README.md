# Panakoes

> All-hearing audio capture, transcription, and insights. The open-source cloud platform powering the LaFayette Labs wearable and any other audio source.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Status](https://img.shields.io/badge/status-pre--alpha-red)
[![Python tests](https://github.com/Aztec03hub/panakoes/actions/workflows/pytest.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/pytest.yml)
[![TypeScript tests](https://github.com/Aztec03hub/panakoes/actions/workflows/vitest.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/vitest.yml)
[![Terraform CI](https://github.com/Aztec03hub/panakoes/actions/workflows/terraform-ci.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/terraform-ci.yml)
[![CodeQL](https://github.com/Aztec03hub/panakoes/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/codeql.yml)
[![License check](https://github.com/Aztec03hub/panakoes/actions/workflows/license-check.yml/badge.svg?branch=main)](https://github.com/Aztec03hub/panakoes/actions/workflows/license-check.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Aztec03hub/panakoes/badge)](https://scorecard.dev/viewer/?uri=github.com/Aztec03hub/panakoes)

## Overview

Panakoes ingests audio (uploaded files or live wearable streams), transcribes it with state-of-the-art speech recognition, and extracts AI-powered summaries and action items. Built on AWS with a pluggable transcription backend, audit-ready architecture, and self-service operations tooling.

The project is the first open-source initiative under [LaFayette Labs LLC](https://lafayettelabs.com) and serves as the cloud backend for an upcoming AI wearable (a Plaud Note Pro competitor, forking [Omi](https://github.com/BasedHardware/Omi) by BasedHardware).

The project name is constructed Greek for "all-hearing", a deliberate parallel to Argus Panoptes ("all-seeing").

## Features

- **Async batch transcription.** Upload audio, get a transcript and summary back in minutes.
- **Live streaming transcription.** Sub-second latency via session-spawned GPU instance.
- **Pluggable transcription backends.** Designed for the SOTA model that wins next year, not just today's.
- **AI-powered summaries and action items.** Claude Haiku for fast standard summaries; Claude Sonnet for deep premium summaries on paid tiers.
- **Stripe-billed subscription tiers.** Free, Pro ($12/mo), Team ($30/seat/mo with 3-seat minimum).
- **Self-service admin dashboard.** Real-time health, cost tracking, lifecycle controls behind step-up MFA.
- **Comprehensive audit trail.** Application-level events plus AWS-API-level events.
- **OpenTelemetry instrumented.** Vendor-neutral observability with AWS-native backends (CloudWatch, X-Ray).

## Architecture

A detailed architecture write-up lives in [`docs/architecture.md`](docs/architecture.md).

**High-level:** Event-driven microservices on AWS.

- **Async path:** S3 upload event → Lambda (validate + enqueue) → AWS Batch with EC2 Spot GPU running Whisper-large-v3 → S3 transcript → DynamoDB stream → Lambda summarizer (Claude) → RDS metadata → notification.
- **Streaming path:** API Gateway WebSocket → Session Manager (Lambda) → Spawner (Lambda) → ECS-managed g4dn.xlarge Spot GPU spawned per session → faster-whisper-large with Silero VAD streaming output → WebSocket → client.

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Cloud | AWS (us-east-1) | Deep service coverage; portfolio-aligned with Solutions Architecture roles |
| IaC | Terraform | Declarative, version-controlled, multi-cloud-portable |
| Languages | Python (services), TypeScript (auth) | Polyglot microservices, right tool for each job |
| Frontend | SvelteKit on S3 + CloudFront | Performance, simplicity, AWS-native hosting |
| Auth | Better-Auth | Modern, RBAC + MFA + step-up auth support |
| Transcription | Whisper-large-v3 (fp16) on GPU; pluggable | SOTA accuracy with model-swap pathway |
| AI summarization | Claude Haiku (default), Claude Sonnet (premium) | Cost discipline + paid-tier differentiator |
| Observability | CloudWatch + X-Ray + OpenTelemetry | AWS-native backends, vendor-neutral instrumentation |
| Logging | CloudWatch Logs → S3 archive → Athena | Cheap and queryable at any timescale |
| Audit trail | DynamoDB custom log + CloudTrail | App-level + AWS-API-level coverage |
| Payments | Stripe | Real billing experience, industry standard |
| Tests | pytest, vitest, Playwright | Layered test pyramid with real-DB integration tests |

## Quick Start

Setup instructions land here as the project takes shape. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the current developer environment notes.

### First-time clone setup

```bash
git clone https://github.com/<owner>/panakoes.git
cd panakoes
make setup           # install Python dev deps for every service
make install-hooks   # arm the pre-push hook (runs `make ci-pr` before every push)
```

`make install-hooks` is idempotent and only needs to run once per clone. It points `core.hooksPath` at the version-controlled `.githooks/` directory so the pre-push gate applies automatically. If you skip this step, `make ci-pr` and `make ci-local` will warn you (one-line, non-fatal). Bypass the hook in an emergency with `NO_VERIFY=1 git push`; see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contract.

## Local development

Two paths, pick whichever matches your setup.

**1. Native (WSL2 / Linux / macOS) with Docker installed:**

```bash
make dev-up        # postgres + dynamodb-local
# DEV_LOCALSTACK=1 make dev-up   # also start localstack (S3, EventBridge, SNS, SQS)
make dev-down      # stop the stack
```

`make dev-up` is idempotent and prints the `DATABASE_URL`, `DDB_ENDPOINT_URL`, and `AWS_ENDPOINT_URL` values your services expect. Implementation lives in [`scripts/dev-up.sh`](scripts/dev-up.sh) and [`docker-compose.yml`](docker-compose.yml).

**2. VS Code Codespaces / Dev Containers:**

Open the repo in a Codespace or in VS Code with the Dev Containers extension. The [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json) config provisions Python 3.12, Node 22, Terraform, AWS CLI, and Docker-in-Docker; its `postCreateCommand` installs `uv`, `pnpm@11.0.8`, `gitleaks`, `pre-commit`, and `packer`. Inside the container, run `make dev-up` to start the data services.

VS Code-specific tooling (extension recommendations, format-on-save, per-service Python interpreter selection) lives in [`.vscode/`](.vscode/) and applies to non-Codespaces users too.

## Project Structure

```
panakoes/
├── services/            Python and TypeScript microservices
├── infra/               Terraform modules and environment configs
├── web/                 SvelteKit frontend
├── tests/               Cross-service integration and e2e tests
├── docs/                Architecture, decisions, application drafts
└── .github/             CI workflows, issue and PR templates
```

## Documentation

- [`PLANNING.md`](PLANNING.md): architectural decisions, rationale, evolution
- [`SCOPE.md`](SCOPE.md): MVP scope and phase-2 backlog
- [`docs/STATUS.md`](docs/STATUS.md): live "where we are right now" snapshot (deployed services, deployed infra, open backlog, known partial states). Read this BEFORE picking up work.
- [`docs/operator/aws-cloudflare-actions.md`](docs/operator/aws-cloudflare-actions.md): operator runbook for AWS + Cloudflare manual actions (the "what's waiting on a human" board).
- [`CHANGELOG.md`](CHANGELOG.md): release history
- [`SECURITY.md`](SECURITY.md): security policy and disclosure
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how to contribute
- [`CLAUDE.md`](CLAUDE.md): Claude Code project conventions
- [`docs/architecture.md`](docs/architecture.md): detailed architecture write-up
- [`docs/adr/`](docs/adr/): Architecture Decision Records (ADR-021 onward; earlier ADRs in `PLANNING.md`)
- [`docs/runbooks/`](docs/runbooks/): disaster recovery, incident response, dev troubleshooting

## License

MIT. See [`LICENSE`](LICENSE).

## About LaFayette Labs

Panakoes is developed by LaFayette Labs LLC, an Illinois-registered hardware and software lab focused on AI-augmented sensor and audio systems.
