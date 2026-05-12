# Runbooks

Operational runbooks for Panakoes. Each runbook covers a specific class of problem with concrete commands, decision flowcharts, and rollback steps. Runbooks are living documents; update the relevant runbook in the same PR cycle as the incident or change that proved it incomplete.

## Index

| Runbook | One-line description |
|---|---|
| [`disaster-recovery.md`](disaster-recovery.md) | Recover from foundational data or infra loss: Terraform state corruption, RDS PITR, DynamoDB PITR, S3 versioning rollback, ECR image retag, GitHub repo recovery. |
| [`incident-response.md`](incident-response.md) | Detect, triage, communicate, mitigate, and post-mortem any production or dev incident; severity matrix and rollback procedures (PR revert, CloudFront invalidation, Lambda alias rollback). |
| [`dev-troubleshooting.md`](dev-troubleshooting.md) | Resolve local dev tooling friction: nvm/Node, uv, pre-commit hooks, pnpm `onlyBuiltDependencies`, testcontainers on WSL2, gitleaks false positives, Terraform lock conflicts, `merge=union` for CHANGELOG. |
| [`auth-db-first-deploy.md`](auth-db-first-deploy.md) | One-time per-environment procedure to bring up the auth Postgres schema on a fresh Aurora cluster: register the new task-def revision, create the migrate-time secret, run the migrator via `aws ecs run-task`, create the least-privileged `auth_app` role and grants, swing `panakoes-dev/database-url` to `auth_app`, force-redeploy, smoke test, seed the first admin. Applies ADR-039. |
| [`gpu-ami-bake.md`](gpu-ami-bake.md) | Bake (and rotate) the streaming + batch GPU AMI defined in `infra/ami/gpu-transcribe/`. Covers the `PendingVerification` pre-flight probe pattern, model artifact staging (Whisper-large-v3, faster-whisper-large, Silero VAD), Packer build invocation, AMI ID pin in `infra/dev/batch/variables.tf`, and AMI + snapshot rollback. |
| [`long-audio-smoke.md`](long-audio-smoke.md) | End-to-end smoke test for the `panakoes-dev-long-audio` Step Functions state machine. Three lanes (A: state machine reachability with no Lambdas, B: DetectDuration only, C: full pipeline) covering S3 stub upload, `start-execution` invocation, terminal-state inspection, and cleanup. Documents the expected execution graph + the IAM/log-group surface verified by each lane. |
| [`ses-bootstrap.md`](ses-bootstrap.md) | Bring a fresh environment from zero to "the notification service can send a transactional email": SES domain identity (DKIM via Cloudflare DNS), sandbox-mode recipient verification, IAM-user-based SMTP credential derivation, Secrets Manager population, smoke send, and the sandbox-to-production exit checklist. |
| [`aurora-restore-drill.md`](aurora-restore-drill.md) | Quarterly drill that validates the dev auth Aurora cluster's PITR posture end to end: pre-flight (vault + retention + password-rotation timing), `restore-db-cluster-to-point-in-time` clone, writer-instance creation, `psql` row-count probe via one-off Fargate task, prod-baseline comparison, mandatory cleanup. Cost under $0.10 per drill, ~25 to 30 minutes wall clock. Cross-referenced from `disaster-recovery.md` Lane B. |
| [`async-transcription-smoke.md`](async-transcription-smoke.md) | End-to-end smoke for the async transcription path. Two lanes: Lane A drives the transcribe-worker Lambda (Groq backend) via an S3 ObjectCreated -> EventBridge -> SQS -> Lambda chain; Lane B submits a GPU Batch job against `panakoes-dev-transcribe-queue`. Covers PendingVerification fall-through, the pending Batch CE -> bespoke GPU AMI roll, and current deployment status of every component on the path. |
| [`admin-dashboard-e2e-smoke-test.md`](admin-dashboard-e2e-smoke-test.md) | Post-deploy smoke that exercises all four layers of the admin dashboard (SPA build + CloudFront, API Gateway HTTP API v2 with the (c+) routing shape per ADR-038, backend services auth + cost-api + admin-api, Aurora + DDB). Includes pre-flight platform checks, curl steps for each layer, a Playwright reference, expected timings, a smoke-user cleanup query, and a failure-mode map keyed by symptom. |
| [`streaming-websocket-smoke.md`](streaming-websocket-smoke.md) | Smoke test the streaming WebSocket API (`panakoes-dev-streaming-ws`): connect, send one frame per route key (`$connect`, `$disconnect`, `$default`, `audio-frame`, `transcript-request`), poll the frame SQS queue to confirm dispatch, inspect access logs. Documents the Lambda authorizer JWT shape (HS256 via `panakoes-auth-client`) the follow-up PR will validate. |
| [`cost-allocation-tag-activation.md`](cost-allocation-tag-activation.md) | One-time per-tag activation flow at the Billing console (Billing > Cost allocation tags > User-defined cost allocation tags) for the tags introduced by ADR-043. Covers locating the new tag rows, the **Activate** click for `Service` / `Environment` / `Component`, the ~24-hour Cost Explorer backfill window, and verification via `aws ce get-cost-and-usage --group-by Type=TAG,Key=Service` + a DynamoDB scan against `panakoes-dev-tenant-cost-rollup`. |
| [`streaming-websocket-smoke.md`](streaming-websocket-smoke.md) | Smoke test the streaming WebSocket API (`panakoes-dev-streaming-ws`): connect, send one frame per route key (`$connect`, `$disconnect`, `$default`, `audio-frame`, `transcript-request`), poll the frame SQS queue to confirm dispatch, inspect access logs. Documents the Lambda authorizer JWT shape (HS256 via `panakoes-auth-client`) the follow-up PR will validate. |

## How to use the runbooks

1. **Pick the right runbook by symptom, not by guess.** "Customer can't log in" is `incident-response.md`; "I can't commit because the em-dash hook is angry" is `dev-troubleshooting.md`; "the Terraform state file got truncated" is `disaster-recovery.md`. The "When to use this runbook" section at the top of each file disambiguates.
2. **Follow the procedure step by step.** The commands are concrete on purpose; do not improvise.
3. **Verify after.** Each runbook ends with a Verification section. Do not declare resolution before it passes.
4. **Update the runbook if it was wrong.** Per the discipline in `incident-response.md` post-incident actions, an incident is not closed until any runbook gap is fixed.

## Authoring instructions for new runbooks

Every Panakoes runbook follows this structure. Copy the template below to create a new runbook; update this index once it lands.

### Template

```markdown
# <Runbook Title>

## Purpose

One paragraph: what problem does this runbook solve and why does it exist?

## When to use this runbook

Bulleted list of concrete trigger conditions. Be specific. If a condition belongs in another runbook, say so and link.

## Prerequisites

What the operator needs before starting (CLI tools, credentials, access roles, environment). List concrete commands to verify each prerequisite where possible.

## Procedure

Numbered steps. Each step has:
- A clear action verb (`Run`, `Inspect`, `Confirm`, `Update`).
- A concrete command (not vague guidance).
- An expected outcome.

For runbooks covering multiple distinct failure modes, use sub-sections (or "lanes") rather than one giant flat list. Add a decision flowchart at the top of the procedure to route the operator into the right lane.

## Verification

How the operator knows the procedure worked. Concrete checks: a command's output, an alarm state, a smoke test, a query against the audit log. No vague "looks good".

## Rollback

How to undo this runbook's actions if they made things worse. Most procedures should be reversible; document the inverse explicitly. Where an action is irreversible (data deletion, certificate rotation), flag it loudly in the Procedure section.

## References

- Relevant ADRs in `PLANNING.md` (ADR-001 through ADR-020) or `docs/adr/` (ADR-021 onward).
- Relevant `CLAUDE.md` sections (link by section heading).
- Other runbooks in this directory.
- Memory entries (`feedback_panakoes_lessons`, `workflow_panakoes_pr_flow`, etc.) when applicable.
- External docs (AWS service docs, vendor status pages, upstream tool docs) where the operator may need to follow links.
```

### Style rules

These match the project-wide rules in `CLAUDE.md` "Phil's Voice Rules" and `docs/adr/README.md` "Style rules":

- **No em-dashes (U+2014) or en-dashes (U+2013), ever.** Use commas, periods, parentheses, semicolons, or hyphens. The pre-commit hook `scripts/check_no_em_dashes.sh` enforces this.
- **Direct, concise prose.** No marketing fluff. No "leverage", no "robust", no "seamless".
- **Concrete commands over vague guidance.** "Run `aws s3 ls s3://...`" beats "check the bucket".
- **Cite specifics.** Reference exact file paths, ADR IDs, table names, SNS topic names, environment names. The reader is mid-incident; they should not have to guess.
- **Ordered steps for procedures.** Use numbered lists for any sequence the operator must perform in order. Bullets are for unordered references.

### Adding the runbook to this index

When a new runbook ships:

1. Add a row to the Index table above with the path and a one-line description.
2. Cross-link from any other runbook that should reference it (often `incident-response.md`).
3. Land it in a `docs:` PR. Per the changelog-check workflow's exempt list, `docs/*` PRs do not require a CHANGELOG entry.
