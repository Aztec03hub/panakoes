# ADR-033: Tier 3 lifecycle response-code semantics

## Status

Accepted. Lived since 2026-05-09 (Phase 3.1 of the admin dashboard, alongside ADR-032). Codifies the response-code design choice that ADR-032 alluded to in its "Decision" section but did not formally enumerate.

## Context

ADR-032 established the four-layer Tier 3 safety pattern (typed confirmation, idempotency-by-key, audit-before-AND-after, step-up MFA). It described the layers but did not nail down what an HTTP client sees when each layer fires.

A client receiving a Tier 3 lifecycle response needs to answer two distinct questions:

1. **Did my request even get processed?** Auth could be missing, the role could be wrong, the step-up MFA window could be stale, the typed confirmation string could be wrong, the idempotency_key could be malformed. None of these conditions involve the operation handler running. Nothing was audited; no `lifecycle-state` row exists.
2. **If it was processed, did the operation succeed?** Once the gates pass, the lifecycle protocol always completes: the audit `intent` row is written, the handler runs (or raises), the audit `outcome` row records success or failure, the `lifecycle-state` row is finalized.

Conflating these into a single status code loses information. Routine HTTP middleware (load balancers, alarms, dashboards, log-based metrics) reads status codes; if a "your auth is missing" response and a "your operation crashed" response collapse to the same code, the middleware cannot tell them apart, RBAC observability degrades, and operator dashboards cannot render the right call-to-action.

The split also matters for idempotency replay. A client that retries the same `idempotency_key` should get the cached envelope back regardless of whether the original attempt succeeded or failed; otherwise a flaky network turns a recorded failure into a re-running side effect.

## Decision

Tier 3 lifecycle routes return responses split along the pre-flight / post-protocol-start boundary. Four response classes:

| Status code | Body | Meaning |
|---|---|---|
| 401 / 403 | `{"detail": "..."}` | **Pre-flight auth gate failed.** Missing or invalid JWT (401), wrong role or stale step-up MFA (403). Lifecycle protocol never started. Nothing audited. No `lifecycle-state` row. |
| 400 | `{"detail": "..."}` | **Pre-flight validation gate failed.** Confirmation string mismatch, malformed `idempotency_key`, missing required params. Lifecycle protocol never started. Nothing audited. No `lifecycle-state` row. |
| 200 | `LifecycleResponse` with `status="succeeded"`, `result=...` | **Protocol succeeded, operation succeeded.** Both audit rows written; `lifecycle-state` row finalized to `SUCCEEDED`. |
| 200 | `LifecycleResponse` with `status="failed"`, `error_message=...` | **Protocol succeeded, operation handler raised.** Both audit rows written (the outcome row records `outcome="failure"` and the exception type); `lifecycle-state` row finalized to `FAILED`. |
| 200 | `LifecycleResponse` with `status="cancelled"` | **Reserved.** Not used today. Held for a future cancellable long-running operation; the enum value exists in `LifecycleStatus` so downstream consumers can pattern-match without a code change. |

The cut: 4xx means "your request was rejected before any protocol step ran." 200 means "the protocol ran end-to-end; read the envelope to learn the outcome."

Implementation lives in `services/admin-api/src/panakoes_admin_api/safety.py`. The `execute_lifecycle` function raises `HTTPException` for pre-flight gate failures (FastAPI translates these into 4xx responses) and raises `_LifecycleHandlerFailed` (a private sentinel) for handler failures, carrying the already-finalized envelope. The wrapper `execute_lifecycle_or_failed_envelope` catches the sentinel immediately outside the audit context manager and returns the envelope as a normal 200, letting `HTTPException` propagate untouched.

## Consequences

**Positive.**

- The dashboard handles the response with a single conditional: "if status code is 4xx, render `detail` as a fix-the-request error message; if status code is 2xx, render the envelope (success table or failure call-to-action depending on `envelope.status`)."
- RBAC observability works. CloudWatch metrics on 401 / 403 rates surface auth misconfiguration without false positives from "operation failed at the application layer."
- Idempotency works for the failed case too. Replaying the same `idempotency_key` after a handler failure returns the cached failed envelope (status code 200, `status="failed"`) rather than re-running the handler. The recorded failure is sticky; the operator decides whether to retry under a new `idempotency_key` or root-cause first.
- Audit-log invariants hold. Every 200 response (success or failed) corresponds to exactly one `intent` + one `outcome` row pair. Every 4xx response corresponds to zero audit rows. There is no third state.
- Adding a new lifecycle operation in Phase 3.2 inherits the response-code contract for free; the route shim does not need to think about it.

**Negative.**

- The 200-with-failed-status convention is unusual. Standard REST convention often maps "the operation failed" to a 5xx. Engineers new to the codebase must learn that for Tier 3, the HTTP status reports protocol-level success (the gates passed and the audit trail closed cleanly) and the body reports operation-level outcome. Documented here, in ADR-032, in the runbook, and inline at the top of `safety.py`.
- A 200-with-failed-status response cannot be acted on by a generic HTTP retry middleware (e.g., a client library that retries on 5xx). This is intentional: Tier 3 operations are dangerous, and we explicitly do not want a generic retry layer re-running them. Retries must be deliberate, with a fresh `idempotency_key`, by an operator looking at the failed envelope.
- Tools that aggregate "failure rate" by status code (basic CloudWatch metric filters, naive dashboards) will under-report Tier 3 failures unless they also parse `envelope.status`. The audit log carries the authoritative outcome record; metric pipelines that need accuracy should read from the audit log, not the HTTP access log. Documented in the runbook.

## Alternatives considered

**Always 200; encode all status (auth, validation, outcome) in the body.** Rejected. Routine HTTP middleware can no longer distinguish auth failures from operation failures from successful operations. RBAC observability degrades; load balancers cannot fail-closed on auth misconfiguration; CloudWatch alarms on 4xx rates lose meaning. The cost of preserving the 4xx semantic is one branch in the dashboard renderer and one paragraph of doc; cheap.

**5xx for handler failures.** Rejected. The lifecycle protocol DID succeed end-to-end: gates passed, audit rows wrote, `lifecycle-state` finalized. Only the operation outcome was a failure. Returning 5xx (a) misrepresents the protocol state, (b) invites generic retry middleware to re-run the operation under a different code path that bypasses our deliberate retry semantics, (c) breaks idempotency replay because most retry layers refuse to cache 5xx responses. The 200 status is a precise statement: "the protocol you asked me to run, ran." The body is the report.

**207 multi-status.** Rejected as overkill. WebDAV's 207 is designed for batch endpoints that report mixed-outcome results across many sub-operations. A single Tier 3 route covers one logical operation; even `block-user-sessions` (which fan-outs across N session rows) returns one envelope describing the aggregate outcome. The complexity of 207 (XML / structured sub-status arrays, RFC-defined parser semantics) buys us nothing.

**Distinguish auth failures (401 / 403) from validation failures (400) by separate exception types.** Already in place; 401 (no/invalid JWT) and 403 (wrong role or stale step-up) come from the auth dependency layer; 400 (confirmation mismatch, missing params) comes from `execute_lifecycle` and Pydantic body validation. Documenting it here so the table above is the canonical reference.

## References

- [`ADR-032: Tier 3 lifecycle safety pattern`](ADR-032-tier-3-lifecycle-safety-pattern.md) (the parent ADR; this ADR refines its response-code section)
- `services/admin-api/src/panakoes_admin_api/safety.py` (the orchestrator, response-code split documented inline)
- `services/admin-api/src/panakoes_admin_api/routes/lifecycle.py` (the route shims demonstrating the pattern)
- `services/admin-api/src/panakoes_admin_api/lifecycle_state.py` (the `LifecycleStateStore` that finalizes envelopes to terminal status)
- `services/admin-api/src/panakoes_admin_api/models.py` (`LifecycleStatus`, `LifecycleResponse` envelope shape)
- `docs/operations/tier-3-runbook.md` (operator-facing companion; "Common failure modes and recovery" section maps each response class to remediation)
