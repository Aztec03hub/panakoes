# Architecture Decision Records

This directory holds the formal Architecture Decision Records (ADRs) for Panakoes. Each ADR captures one consequential decision, its context, the alternatives considered, and the consequences of the chosen path.

`PLANNING.md` at the repo root holds a fast-lookup decision register and longer-form rationale for the early ADR-001 through ADR-020 entries (which predate this directory). New ADRs from ADR-021 onward live here as individual files.

## Index

| ID | Title | Description |
|---|---|---|
| ADR-021 | [Worktree Convention for Parallel Sub-Agents](ADR-021-worktree-convention-for-parallel-agents.md) | Concurrent sub-agents MUST run in dedicated git worktrees branched from `origin/main`. |
| ADR-022 | [JWT Signing, HS256 in Slice 1 then RS256 + JWKS in Slice 2](ADR-022-jwt-hs256-then-rs256.md) | Auth service signs HS256 with a shared secret in slice 1; migrates to RS256 + JWKS for production credibility in slice 2. |
| ADR-023 | [Audit Library with Three Backends](ADR-023-audit-library-three-backends.md) | `panakoes-audit` ships Memory, Stdout, and DynamoDB backends behind a single `AuditStore` Protocol, selected by env var, gated at 100% coverage. |
| ADR-024 | [Orchestrator-Delegation as Default Working Mode](ADR-024-orchestrator-delegation-pattern.md) | Top-level Claude decomposes work into focused briefs, spawns parallel sub-agents in worktrees, verifies output against the brief and the run report, integrates only verified work. |
| ADR-025 | [Agent Run Report Schema](ADR-025-agent-run-report-schema.md) | Every agent invocation that touches files emits a structured report at `.agent-runs/<UTC-timestamp>-<slug>.md` with YAML frontmatter and a markdown body. |
| ADR-026 | [CHANGELOG.md Merge=Union](ADR-026-changelog-merge-union.md) | `.gitattributes` declares `CHANGELOG.md merge=union` so concurrent appends to `[Unreleased]` stop producing conflicts. Scoped narrowly to CHANGELOG.md. |
| ADR-027 | [CI Workflow Concurrency: Cancel In Progress](ADR-027-ci-workflow-concurrency-cancel-in-progress.md) | Stale CI runs cancel when a newer commit lands on the same PR. |
| ADR-028 | [auto-update-prs PAT Authentication](ADR-028-auto-update-prs-pat-authentication.md) | Cascade-rebase workflow uses a fine-grained PAT, not `GITHUB_TOKEN`, to bypass the no-self-approval recursion. |
| ADR-029 | [Dependabot Grouped Minor + Patch](ADR-029-dependabot-grouped-minor-patch.md) | Dependabot groups minor + patch updates per ecosystem to keep PR volume tractable. |
| ADR-030 | [Ruleset Bypass Actor for Emergencies](ADR-030-ruleset-bypass-actor-for-emergencies.md) | Phil's user is configured as a ruleset bypass actor for documented emergency scenarios. |
| ADR-031 | [cost-api Read-Through Cache](ADR-031-cost-api-read-through-cache.md) | cost-api routes use a DynamoDB read-through cache layered over Cost Explorer to keep CE call counts bounded. |
| ADR-032 | [Tier 3 Lifecycle Safety Pattern](ADR-032-tier-3-lifecycle-safety-pattern.md) | Tier 3 lifecycle ops require typed-confirmation + step-up MFA + idempotency-key + before/after audit + protocol-failure-as-200. |
| ADR-033 | [Tier 3 Response Code Semantics](ADR-033-tier-3-response-code-semantics.md) | Lifecycle protocol failures return 200 OK with `status: failed` discriminator; transport failures return 4xx/5xx. |
| ADR-034 | [CloudFront Standard Logs v2](ADR-034-cloudfront-standard-logs-v2.md) | CloudFront access logs use CloudWatch Logs Delivery to S3 (v2), not the legacy S3 + ACL path (v1), so the bucket stays on the secure `BucketOwnerEnforced` default. |
| ADR-035 | [New AWS Account Friction Mitigations](ADR-035-new-aws-account-friction-mitigations.md) | Every new LaFayette Labs AWS account or new region performs a "warm-up" within the first week: trip the EC2 `PendingVerification` gate via a throwaway `t3.micro`, and pre-request likely-zero GPU vCPU quotas (`L-DB2E81BA`, `L-3819A6DF`). |
| ADR-036 | [Aurora Serverless v2 Scale-to-Zero for the Dev Tier](ADR-036-aurora-serverless-v2-scale-to-zero.md) | Dev Aurora Serverless v2 cluster runs with `min_capacity = 0` + `seconds_until_auto_pause = 300` for $0/mo idle; production overrides via `var.min_capacity_acu`. |
| ADR-037 | [Pluggable Transcriber Abstraction with Three Concrete Backends](ADR-037-pluggable-transcriber-three-backends.md) | Ship and maintain GroqTranscriberBackend, OpenAITranscriberBackend, and (planned) WhisperGPUTranscriberBackend concurrently behind the `Transcriber` Protocol; consumer selects via `TRANSCRIBER_BACKEND` env var. |

## Adding a new ADR

1. **Pick the next number.** Look at the highest existing ADR number across this directory and `PLANNING.md`'s decision register; increment. Do not reuse numbers, even for superseded ADRs.
2. **Create the file** at `docs/adr/ADR-<number>-<kebab-case-title>.md`.
3. **Use the template below.** Every ADR has Title, Status, Context, Decision, Consequences, References.
4. **Update this README's index table** with the new entry.
5. **If the ADR supersedes a prior decision,** mark the prior one `SUPERSEDED BY ADR-<new>` (in `PLANNING.md` for ADR-001 through ADR-020, or in the prior ADR file's Status section for newer ADRs). Do not delete superseded records; they document the evolution.
6. **Land it in a `docs:` PR.** Per the changelog-check workflow's exempt list, a `docs/*` PR does not require a CHANGELOG entry.

## Template

```markdown
# ADR-<number>: <Title>

## Status

<Proposed | Accepted | Deprecated | Superseded by ADR-XXX>

## Context

<What forces are at play? What constraints exist? What problem are we solving? What alternatives exist?>

## Decision

<The chosen path. Be specific. Include the operational details a future reader needs to apply the decision.>

## Consequences

<Positive and negative consequences of the decision. What becomes easier? What becomes harder? What follow-up work does the decision imply?>

## References

<Code paths, prior ADRs, external docs, incident reports, anything a reader would want to chase.>
```

## Style rules

- **No em-dashes,** ever. Use commas, periods, parentheses, or semicolons. Hard rule across the project.
- **Direct, concise prose.** No marketing fluff.
- **Cite specifics.** Reference exact file paths, ADR IDs, PR numbers, and dates wherever possible.
