# Panakoes

> All-hearing audio capture, transcription, and insights. The open-source cloud platform powering the LaFayette Labs wearable and any other audio source.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Status](https://img.shields.io/badge/status-pre--alpha-red)
![Build](https://img.shields.io/badge/build-pending-lightgrey)

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
- [`CHANGELOG.md`](CHANGELOG.md): release history
- [`SECURITY.md`](SECURITY.md): security policy and disclosure
- [`CONTRIBUTING.md`](CONTRIBUTING.md): how to contribute
- [`CLAUDE.md`](CLAUDE.md): Claude Code project conventions
- [`docs/architecture.md`](docs/architecture.md): detailed architecture write-up

## License

MIT. See [`LICENSE`](LICENSE).

## About LaFayette Labs

Panakoes is developed by LaFayette Labs LLC, an Illinois-registered hardware and software lab focused on AI-augmented sensor and audio systems.
