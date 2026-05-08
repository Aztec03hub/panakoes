# ADR-023: Audit Library with Three Backends

## Status

Accepted.

## Context

ADR-017 commits Panakoes to a DynamoDB-backed application-level audit log alongside AWS CloudTrail. CloudTrail covers AWS-API-level operations; the application audit log covers domain events ("user X created transcript Y", "service Z ran summarization on resource W"). Together they form complete coverage.

ADR-018 places audit code in the 100%-coverage tier alongside auth and billing. CLAUDE.md repeats the gate: audit is security-relevant, so it ships at full coverage.

Every Python microservice writes audit events. The constraints differ by environment:

- **Tests** want fast, deterministic, in-memory writes that the assertion harness can read back. Hitting DynamoDB (even via `moto`) is unnecessary friction in unit tests.
- **Local dev** wants stdout visibility so the developer can see events flow without standing up DynamoDB or polling a remote table.
- **Production** wants durable, queryable writes against the `panakoes-dev-audit-log` (and later prod) DynamoDB table provisioned in Terraform.

A single hardcoded backend would force compromises in at least two of the three contexts. A pluggable backend with a startup-time selector matches the operational reality.

## Decision

`panakoes-audit` (`services/audit-lib/`) ships with three concrete backends behind a single `AuditStore` Protocol:

- **`MemoryAuditStore`**: in-memory `list`; exposes `events` for assertions and a `clear()` helper for test isolation. Default in unit tests.
- **`StdoutAuditStore`**: emits one-line JSON via `print()`. Suitable for local dev, where CloudWatch agents or tail-based tooling pick up stdout. Default for `pnpm dev`-style local runs.
- **`DynamoDBAuditStore`**: writes to the configured DynamoDB table (`panakoes-dev-audit-log` in dev, `panakoes-prod-audit-log` in prod). Default for deployed environments.

Backend selection happens at process startup via the `AUDIT_BACKEND` env var (`memory` | `stdout` | `dynamodb`). The `record_event` API lazily acquires the configured backend on first call and reuses it thereafter.

The `AuditEvent` Pydantic model is the canonical schema across backends. Validation lives once, in the model. Every backend stores or emits exactly the fields the model defines.

Coverage gate: 100%, enforced via `--cov-fail-under=100` in `pyproject.toml`. Audit is security-relevant per CLAUDE.md, and the gate prevents silent regressions.

## Consequences

**Positive:**
- Backend swap is a one-line config change per service. No code changes to switch tests from in-memory to integration-mode DynamoDB-via-moto, or to switch local dev from stdout to a real table for debugging.
- The Pydantic `AuditEvent` model is the single source of truth for the event schema. Backends serialize; they do not validate independently.
- Adding a fourth backend (e.g., a Kinesis stream for real-time event consumers, or a SQS-based async writer) is one new class implementing the `AuditStore` Protocol, plus a config branch and tests. No callers change.
- 100% coverage discipline catches drift early. Audit code is small enough that the gate is sustainable, and large enough that the gate has caught regressions during refactors.

**Negative:**
- Three backends means three code paths to keep correct. Mitigated by the shared `AuditEvent` validation and by the 100%-coverage gate covering all three.
- Backend selection is a startup-time decision. Switching mid-process requires `set_store` / `reset_store` (which exist for tests) and is not generally safe for production.
- Misconfiguring `AUDIT_BACKEND=stdout` in production silently routes audit events to stdout instead of DynamoDB. Mitigated by Terraform-managed env wiring on the deployed task definitions, which sets `AUDIT_BACKEND=dynamodb` explicitly.

## References

- `services/audit-lib/`, full implementation, including `MemoryAuditStore`, `StdoutAuditStore`, `DynamoDBAuditStore`, the `AuditStore` Protocol, the `AuditEvent` Pydantic model, and the `record_event` / `set_store` / `reset_store` public surface.
- `services/audit-lib/README.md`, public API, env vars, DynamoDB schema (`pk = "AUDIT#" + source_service + "#" + actor_id`, `sk = timestamp_iso + "#" + request_id`).
- ADR-017 in `PLANNING.md` (audit trail = DynamoDB custom log + AWS CloudTrail).
- ADR-018 in `PLANNING.md` (testing + 100% coverage on auth/billing/audit).
- CLAUDE.md, "Coverage gates" row of the locked decisions table.
