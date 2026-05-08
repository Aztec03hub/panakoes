# CLAUDE.md: Panakoes Project Conventions

This file is read by Claude Code on every session. It captures the durable conventions, locked decisions, and working patterns for the Panakoes project. Update it whenever a major decision changes; treat it as living documentation, not one-time setup.

---

## Project Snapshot

**Panakoes** is the cloud audio capture, transcription, and insights platform for LaFayette Labs LLC. The name is constructed Greek for "all-hearing", a parallel to Argus Panoptes ("all-seeing").

**Why it exists:**
1. First open-source project under LaFayette Labs LLC (filed 2026-04-23).
2. Doubles as the cloud backend for an upcoming AI wearable (a Plaud-killer, forking Omi).
3. Demonstrates AWS solutions-architect depth for Phil's job search portfolio.

**Home:** Public on Phil's personal GitHub at the `panakoes` repository. Mirrorable to a future LaFayette Labs GitHub organization.

**Domain:** [lafayettelabs.com](https://lafayettelabs.com) (LLC) and [panakoes.com](https://panakoes.com) (project), both registered at Cloudflare 2026-05-07.

---

## Locked Architectural Decisions

| Area | Decision | Why |
|---|---|---|
| Project name | Panakoes | Constructed Greek for "all-hearing"; parallels Panoptes |
| License | MIT | Matches Omi (which we fork for the wearable) |
| AWS region | us-east-1 | Cheapest, widest service coverage |
| IaC tool | Terraform | Industry standard; named in Scigon JD as preferred |
| Auth | Better-Auth | Modern, JWT-based, RBAC + step-up MFA support |
| Repo structure | Monorepo | Faster for week-1 build; polyrepo split later if scale demands |
| Frontend | SvelteKit on S3 + CloudFront | Phil's strongest frontend; AWS-native hosting |
| Languages | Python (most services), TypeScript (auth service) | Polyglot microservices; right tool for each job |
| Transcription mode | Dual-mode (async batch + live streaming) with pluggable Transcriber abstraction | Cost-efficient, model-agnostic |
| Async transcription | AWS Batch + EC2 g4dn.xlarge Spot, Whisper-large-v3 fp16 | Cheap GPU compute; pennies per audio hour |
| Streaming transcription | Session-spawned g4dn.xlarge Spot via custom AMI, faster-whisper-large + Silero VAD, streaming over WebSocket | Sub-second latency once warm |
| Long-audio chunking | Step Functions fan-out for files > 10 minutes | Bypasses Lambda 15-min ceiling |
| AI summarization | Claude Haiku 4.5 (default), Claude Sonnet 4.6 (paid-tier "deep summary" feature) | Cost discipline + tier differentiator |
| Payments | Stripe (Free / Pro $12mo / Team $30/seat min 3) | Real billing, competitive entry pricing |
| Observability | CloudWatch + AWS X-Ray (OpenTelemetry via ADOT) | AWS-native backends; vendor-neutral instrumentation |
| Logging | CloudWatch Logs (30-day) → S3 archive → Athena | Cheap, queryable at any timescale |
| Audit trail | DynamoDB custom log + AWS CloudTrail | App-level + AWS-API-level coverage |
| Testing | pytest + pytest-asyncio + httpx + testcontainers + moto (Python); vitest + msw (TS); Playwright (e2e) | TDD discipline; real DB in integration tests |
| Coverage gates | 80% on services; 100% on auth/billing/audit; 70% on infra | CI fails PR if below |
| MVP scope | v0.1 includes async + streaming both | Ambitious but high portfolio value (see SCOPE.md) |

For full architectural detail, rationale, and ADR-style decision records, see [`PLANNING.md`](PLANNING.md).

---

## Working Modes

### Default mode: Orchestrator-Delegation

Top-level Claude (orchestrator) decomposes work into focused sub-tasks, delegates each to a parallel sub-agent via the Agent tool, monitors progress, verifies output against acceptance criteria, integrates results, and surfaces only verified work to Phil.

**Every Agent tool call MUST:**

1. Tell the sub-agent to first read `CLAUDE.md` so it inherits all conventions before touching code.
2. Include a self-contained task brief with concrete acceptance criteria.
3. Reiterate the non-negotiables inline: Conventional Commits, CHANGELOG update, no secrets, branch-and-PR, TDD requirements.
4. Specify the agent's scope: which files it can touch, what it can install, whether it can push or only stage.
5. **Require the agent to emit a structured run report** at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per the format documented in `.agent-runs/README.md`. The report is the agent's final output; it is not optional.

**After a sub-agent returns, the orchestrator MUST:**

1. **Read the run report** at `.agent-runs/<run-file>.md`. Confirm `status: success` (otherwise re-delegate or escalate). Confirm files_created / files_modified match the actual diff via `git status` / `git diff`.
2. Check the diff matches the brief and stays in scope.
3. Confirm `CHANGELOG.md` was updated, or that the change qualifies for `docs:` / `chore:` skip.
4. Confirm commit messages follow Conventional Commits.
5. Confirm tests were added for new behavior and pass.
6. Run `gitleaks` against the diff.
7. Run lint and type-check on changed files.
8. Review the report's "Decisions Beyond the Brief" section. Surface any judgment calls that warrant Phil's attention before integration.
9. Confirm the report's "Rollback Procedure" is concrete and testable.
10. Reject and re-delegate if any gate fails. Don't rationalize; re-do.

**Why structured run reports matter (project convention):**
Sub-agents are first-class observable subjects, not opaque black boxes. The report is the agent's audit trail: what it built, what it decided, how to undo it. Combined with the orchestrator's verification, this turns AI-augmented development from "trust the diff" into "verify against the report." See `.agent-runs/README.md` for the required format and the orchestrator verification checklist.

### Direct mode (exception)

When Phil explicitly says "you do this" or the task is too small / too tightly coupled / inherently sequential to delegate (single-line config edits, decision conversations, file reads for orientation), Claude does the work directly with identical discipline.

Default is delegation; direct mode is the explicit exception.

---

## Discipline Rules

### Commits and Pull Requests

- **Conventional Commits** for every commit. Format: `type(scope): subject`. Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `perf`, `build`, `security`.
- **Branch from `main`.** Naming: `feat/<topic>`, `fix/<topic>`, `chore/<topic>`, `docs/<topic>`, `security/<topic>`, `ci/<topic>`.
- **Every change ships via PR**, even self-merged. CI runs on every PR.
- **Squash-and-merge** to `main`; main stays linear.
- **No force-push to `main`**, ever, except a documented secret-scrub emergency.
- **No `git reset --hard` on shared history.** Rollback via `git revert`.

### Releases and Tagging

- **SemVer tags** on meaningful checkpoints: `vMAJOR.MINOR.PATCH`.
- Bump rules: MAJOR for breaking, MINOR for new features, PATCH for fixes.
- Every tag triggers a GitHub Release with auto-generated notes from PRs since the prior tag.

### CHANGELOG and README

- **CHANGELOG.md** updated in `[Unreleased]` for every meaningful change. Categories: Added, Changed, Deprecated, Removed, Fixed, Security.
- **README.md** updated when: a new feature ships, setup steps change, the tech stack changes, a new top-level service is added, or a breaking architectural change lands.
- A GitHub Action gate fails the PR if source code changed but `CHANGELOG.md` did not (skippable for `docs:` / `chore:` PRs via label).

### No Secrets, Ever

- Public repo. Anyone can read the code. **No secrets in source code, ever.**
- All API keys, DB passwords, signing secrets, webhook secrets read from environment variables or AWS Secrets Manager / SSM Parameter Store at runtime.
- `.gitignore` blocks `.env`, `.env.*`, `*.tfstate`, `*.tfstate.backup`, `.terraform/`, `*.pem`, `*.key`.
- `gitleaks` pre-commit hook scans every commit; rejected if a secret is detected.
- GitHub secret scanning + push protection enabled at the repo level (free for public repos).
- Terraform state is remote (S3 + KMS-encrypted + DynamoDB lock). State files never in repo.
- GitHub Actions to AWS via OIDC federation. No long-lived AWS access keys anywhere.

### Testing (TDD where required)

- **Write the test first** for any new business logic, security path, or bugfix.
- **Unit tests** for individual functions and modules.
- **Integration tests** for cross-service or cross-component changes; use `testcontainers-python` for real Postgres / Redis instances. Do NOT mock the database.
- **End-to-end tests** for full user flows via Playwright against a deployed dev environment.
- Coverage gates enforced in CI: 80% on application services, 100% on auth/billing/audit paths, 70% on infrastructure-adjacent code.

### Phil's Voice Rules

- **No em-dashes**, ever. Use commas, periods, parentheses, semicolons. (Hard rule across all of Phil's work.)
- **Direct, concise communication** in commit messages, PR descriptions, doc copy.
- **No marketing fluff** in user-facing copy. Concrete and specific.

---

## Document Map

| File | Purpose |
|---|---|
| [`README.md`](README.md) | Public-facing entry point: what, why, how to use |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog format; updated on every meaningful change |
| [`CLAUDE.md`](CLAUDE.md) | This file; project conventions for Claude Code |
| [`PLANNING.md`](PLANNING.md) | Architecture decisions, rationale, evolution log (running ADR journal) |
| [`SCOPE.md`](SCOPE.md) | MVP scope vs deferred-to-phase-2 |
| [`SECURITY.md`](SECURITY.md) | Vulnerability disclosure, security model, threat model summary |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to contribute, branch/commit conventions, dev setup |
| [`LICENSE`](LICENSE) | MIT license text |
| `docs/architecture.md` | Detailed architecture (services, data flow, AWS map) |
| `docs/aws_activate_application.md` | Draft content for AWS Activate Founders application |
| `services/<name>/README.md` | Per-microservice docs |
| `infra/README.md` | Terraform layout and bootstrap process |
| `.agent-runs/README.md` | Required format for sub-agent run reports + orchestrator verification checklist |

---

## Tooling Map

- **gitleaks**: pre-commit secret scanner
- **terraform**: infrastructure as code
- **awscli**: AWS API access
- **packer**: custom AMI builder for the GPU streaming AMI
- **pnpm**: TypeScript workspace manager (Better-Auth service, frontend)
- **uv** (or `poetry`): Python dependency manager (services)
- **pytest** + **pytest-asyncio** + **pytest-cov**: Python tests
- **vitest**: TypeScript tests
- **Playwright**: end-to-end tests
- **gh** (GitHub CLI): PR creation, repo management
- **whois**: domain availability checks (see workflow_domain_availability_check memory)

Install commands and version pinning live in setup scripts under `scripts/`.

---

## Common Sub-Agent Briefs

When delegating recurring patterns, use these templates as starting points. They evolve as we learn what works.

### Implementing a microservice

```
You are implementing the [SERVICE_NAME] microservice for Panakoes.

PREREQUISITE: First read /mnt/c/Users/plafayette/Documents/Facebook/panakoes/CLAUDE.md and /mnt/c/Users/plafayette/Documents/Facebook/panakoes/.agent-runs/README.md. Follow ALL conventions described therein.

TASK: [specific task description]

ACCEPTANCE CRITERIA:
- [test 1]
- [test 2]
- [test 3]

DISCIPLINE (non-negotiable):
- TDD: write the failing test first, then make it pass.
- Conventional Commits with appropriate type (feat / fix / refactor / etc.).
- Update CHANGELOG.md [Unreleased] under the appropriate category.
- All secrets via env vars or AWS Secrets Manager; no hardcoded values.
- Coverage minimum: 80% on services (100% on auth/billing/audit code).

SCOPE:
- Files you may modify: services/[name]/, tests/[name]/, CHANGELOG.md, services/[name]/README.md.
- Do NOT modify: infrastructure code, other services, top-level docs other than CHANGELOG.
- Work on a feature branch named feat/[name]-<short-desc>; do not push to main.

REQUIRED FINAL OUTPUT: Write a structured run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per the format in `.agent-runs/README.md`. The report has YAML frontmatter (run_id, agent_description, timestamps, status, files_created/modified/deleted, commits_made, verification metrics) and a markdown body with sections: Summary, What I Built, Decisions Beyond the Brief, Issues Encountered, Suggestions for Follow-up, Rollback Procedure. Use UTC timestamps in ISO 8601 format. The report is the orchestrator's audit trail; treat it as a first-class deliverable.

When done, return a brief summary (under 200 words): the path of your run report, confirmation of test results and coverage, and any items in the report that need Phil's review before integration.
```

### Writing tests for existing code

```
You are adding tests for [MODULE / SERVICE] in Panakoes.

PREREQUISITE: First read /mnt/c/Users/plafayette/Documents/Facebook/panakoes/CLAUDE.md and /mnt/c/Users/plafayette/Documents/Facebook/panakoes/.agent-runs/README.md.

TASK: Add [unit / integration / e2e] tests for [target] to bring coverage to [target percent].

ACCEPTANCE CRITERIA:
- All new tests pass locally.
- Coverage on [target file/module] reaches [percent].
- No flaky behavior; deterministic across 10 consecutive runs.
- Integration tests use testcontainers for real DB (no mocking the DB).

DISCIPLINE: same as service-implementation brief.

SCOPE: tests/[area]/ and the target file/module if minor refactors are required for testability.

REQUIRED FINAL OUTPUT: Run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per `.agent-runs/README.md`.
```

### Updating Terraform

```
You are modifying Terraform infrastructure for Panakoes.

PREREQUISITE: First read /mnt/c/Users/plafayette/Documents/Facebook/panakoes/CLAUDE.md, /mnt/c/Users/plafayette/Documents/Facebook/panakoes/.agent-runs/README.md, and infra/README.md.

TASK: [specific infra change]

ACCEPTANCE CRITERIA:
- terraform fmt clean.
- terraform validate clean.
- terraform plan shows only the intended change (no drift, no unintended modifications).
- IAM policies follow least-privilege; no wildcards on resource ARNs.

DISCIPLINE:
- Conventional Commits with type `chore(infra)` or `feat(infra)` as appropriate.
- Update CHANGELOG.md if the change affects user-visible behavior or deployment process.
- Update infra/README.md if a new module is introduced.

SCOPE: infra/ directory only; do not modify application code.

REQUIRED FINAL OUTPUT: Run report at `.agent-runs/<UTC-timestamp>-<short-slug>.md` per `.agent-runs/README.md`.
```

---

## Updates to This File

Update CLAUDE.md in the same PR that lands a major architectural decision change. Treat as living documentation.
