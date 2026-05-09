# ADR-032: Tier 3 lifecycle safety pattern

## Status

Accepted. Lived since 2026-05-09 (Phase 3.1 of the admin dashboard).

## Context

Tier 3 of the admin dashboard exposes the dangerous operations that an on-call operator runs to recover from incidents: drain a service, terminate a streaming session, revoke an API credential, force a password reset, rotate a JWT signing key, etc. By construction these operations have:

- **Large blast radius.** A bad input ends a user session, invalidates a billing token, or rotates a key the entire fleet depends on. Mistakes are not recoverable by a refresh.
- **Operator urgency.** They run during incident response, often at 3am, against an unfamiliar system slice. The operator is tired and the time pressure is real.
- **Production-only stakes.** The whole point of these operations is to act on production. A staging-only Tier 3 doesn't help during a real incident.

Three failure modes follow:

1. **Wrong target.** "Terminate session sess_123" against the wrong session because the operator copy-pasted a different ID. There's no undo.
2. **Replay.** A flaky network or stuck client retries the same operation. Without dedup, drains apply twice, password resets fire twice, audit rows multiply, side effects compound.
3. **Forensic gaps.** An operation crashes mid-execution and the audit trail records "started" but never "finished," or worse, never records the start at all because the audit write was inline in the handler. Post-incident reconstruction becomes guesswork.

## Decision

Every Tier 3 operation routes through a single safety pattern composed of four layers, all enforced by code that lives in `services/admin-api/src/panakoes_admin_api/`:

### 1. Typed confirmation string

The request envelope `LifecycleRequest` includes a `confirmation: str` field. The route layer compares it against an operation-specific expected string (e.g. `"DRAIN streaming/auth-prod"`, `"REVOKE sess_xyz"`) and returns 400 on mismatch. The operator must type the exact string into the UI before the operation can fire. The string includes the target, so a copy-paste from a different operation does not satisfy this gate.

This catches **wrong-target** errors. The friction is intentional; the safety pattern's purpose is to make it impossible to fire a dangerous operation without the operator demonstrating they know what they're firing at.

### 2. Idempotency-by-key

The request envelope includes a caller-minted `idempotency_key: str` (UUID-shape). admin-api writes a `pending` row to the `panakoes-dev-lifecycle-state` DynamoDB table keyed on this UUID before the handler runs, then upgrades to a terminal status (`succeeded` / `failed` / `cancelled`) on completion.

Repeated submissions of the same idempotency_key collapse to a single side-effect: the second submission's pre-flight `get` returns the existing row, and the route returns the cached response without re-running the handler. The caller gets at-most-once semantics for free.

This catches **replay** errors. A flaky network or stuck client can retry as many times as it likes; the operation runs exactly once.

TTL on `expires_at` (24h) is a retention bound, not a correctness bound: idempotency only holds for 24h. Anything longer is treated as a fresh operation (and the audit log retains the permanent record). 24h is well past any retry-storm horizon while keeping the table small.

### 3. Audit-before-AND-after

The `audit_lifecycle` async context manager writes two rows per operation:

```
intent  row (pre-execution)   -> action="tier3.<op>.intent",  outcome="pending"
outcome row (post-execution)  -> action="tier3.<op>.outcome", outcome="success"|"failure"
```

Both rows share a single `request_id` (UUID minted by the wrapper) and both carry the `tier3_action` attribute that backs the `Tier3ActionIndex` GSI on the audit-log table. The body raising re-raises the exception **after** the failure outcome row is written, so a partial failure (handler crashes, container OOM, network flap) leaves a forensically reconstructible trail: the `intent` row is the signal that something started, the `outcome` row (or its absence) tells the operator how it ended.

A single-row "after only" audit loses this signal entirely; a partial failure produces no audit row at all. The two-row pattern is what lets post-incident reconstruction work.

### 4. Step-up MFA freshness gate

`require_admin_with_step_up` is a FastAPI dependency that requires the JWT to carry an `mfa_step_up_at` claim within a configurable freshness window (default 5 minutes, configurable via `STEP_UP_MAX_AGE_SECONDS`). The Auth service issues this claim only after a fresh second-factor challenge.

The window prevents a long-lived admin session token from being trivially abused: an attacker who steals an admin session has at most 5 minutes after the user's last MFA challenge to use it for a Tier 3 operation. Read-only Tier 3 surfaces (the audit-log read view) require admin role only, no step-up; the gate is scoped to operations that mutate state.

## Consequences

**Positive.**

- Wrong-target errors trip the typed-confirmation gate and never reach the handler.
- Replay attacks collapse to a single side-effect; the second-and-later attempts return the cached response.
- Partial failures are forensically reconstructible: the `intent` row pins the start time and target, the `outcome` row records success or failure with the exception type, both are joinable on `request_id`.
- The blast radius of a stolen admin session is bounded to the step-up freshness window for any state-changing operation.
- The pattern is uniform across operations. Adding a new lifecycle operation in Phase 3.2 / 3.2-extended is mechanical: declare the typed `(P, R)` Pydantic models, declare the expected confirmation-string template, write the handler. The safety substrate is reused verbatim.

**Negative.**

- **Friction.** A typed-confirmation string is annoying. Operators will be tempted to disable it during a real incident. We accept this; the design pessimizes for "wrong" over "fast" because wrong is unrecoverable and fast is recoverable by typing four extra words.
- **Audit-log churn.** Two rows per operation doubles audit-log volume. Audit-log volume is not the bottleneck (operations are infrequent and audit retention is bounded by S3 archive lifecycle). Acceptable.
- **TTL lifetime mismatch.** lifecycle-state's 24h TTL is shorter than the Tier3ActionIndex GSI's audit-log retention (years, archived to S3 via DynamoDB Streams). An operator looking at an audit row from last week cannot retrieve the lifecycle-state envelope; they get only the audit trail. We accept this because the audit log carries the full operation envelope as part of the row payload, so the audit row alone is sufficient for forensics.
- **Step-up dependency on Better-Auth.** The gate trusts the `mfa_step_up_at` claim. If the Auth service is compromised or the claim signing key leaks, the gate offers no protection. We accept this as part of the trust boundary already established by JWT-based auth across the rest of the system.

## Alternatives considered

**No safety pattern; trust the operator.** Rejected: a typo at 3am ends a session that should not have ended. The cost of typing four extra words is negligible compared to the cost of a wrong action.

**Confirmation by re-fetching the target.** Considered: instead of a typed string, render the target's metadata (session ID, user email, etc.) and require an explicit "Confirm" click. Rejected for v0.1 because operators in incident-response mode click "OK" reflexively. Typed strings force the operator to stop and read.

**Idempotency without an explicit key (server-derived from request body).** Rejected: replays of the same logical operation with cosmetically different bodies (whitespace, key ordering) would not collapse. Caller-minted UUIDs are explicit and survive serialization differences.

**Audit-after-only (single row).** Rejected: see context. A partial failure leaves no audit row at all if the handler crashes before reaching the audit write. Two rows = one signal of "started" survives even total handler failure.

**Always-on step-up (every Tier 3 read also requires fresh MFA).** Rejected: the audit-log read view is a routine inspection tool, not a state-changing operation. Requiring step-up for read access creates friction without proportional safety gain. Scope the gate to mutations.

**Per-operation step-up freshness window.** Rejected for v0.1: a single uniform window keeps the pattern explainable. If the threat model later separates "drain" (lower stakes) from "rotate JWT signing key" (higher stakes), per-operation windows can be added without changing the substrate.

## References

- `services/admin-api/src/panakoes_admin_api/models.py`
- `services/admin-api/src/panakoes_admin_api/auth.py`
- `services/admin-api/src/panakoes_admin_api/audit.py`
- `services/admin-api/src/panakoes_admin_api/lifecycle_state.py`
- `infra/dev/admin-state/main.tf` (the lifecycle-state table)
- `infra/dev/data/main.tf` (the Tier3ActionIndex GSI on audit-log)
- `docs/design/admin-dashboard-tier-2-3.md`
- `docs/design/tier-2-3-implementation-plan.md` (Phase 3.1)
