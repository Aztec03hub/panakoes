# Tier 3 lifecycle operations runbook

How an on-call operator uses Panakoes's Tier 3 lifecycle controls during incident response: which operation matches which symptom, how the safety pattern protects against typos, how to read the audit log afterward, what each failure response means, and how to extend the surface with a new operation. Operational memory for the dangerous operations; the read-this-at-3am companion to [ADR-032](../adr/ADR-032-tier-3-lifecycle-safety-pattern.md) and [ADR-033](../adr/ADR-033-tier-3-response-code-semantics.md).

---

## TL;DR

- Tier 3 = state-changing operator-only actions with large blast radius (terminate session, force-fail ingestion, block user sessions, rotate keys, etc.).
- Every operation gates on **admin role + step-up MFA within the last 5 minutes + a typed confirmation string + caller-minted `idempotency_key`**. If any gate fails, nothing happens. If the gate passes, the operation runs at most once and writes a forensically reconstructible audit-row pair.
- 4xx response = your request was rejected (fix and retry). 200 response = the protocol ran; read `envelope.status` to learn whether the operation succeeded or failed (per [ADR-033](../adr/ADR-033-tier-3-response-code-semantics.md)).
- There is no undo. Wrong target = call the user, document the mistake, learn for next time.

---

## Section 1 - When to use Tier 3

Tier 3 is incident-response surface, not routine operations. Reach for it when one of the symptoms below matches and the routine recovery path (user self-service, retry-driven recovery, the pipeline's own failure handling) is exhausted or unavailable.

### `terminate-session` - Live streaming session needs an immediate cut

**Symptoms.**

- Compromised user account: support reports a stolen credential, the account is mid-session, you need to disconnect the live transcription socket before more audio is captured.
- Stuck session that the Session Manager's reconciliation tick is not collecting (rare, usually points to a deeper bug; capture forensics first).
- Operator-side cleanup before a hot-fix deploy where draining is too slow.

**Effect.** Marks the row in `panakoes-dev-streaming-sessions` as `errored`, sets `terminated_at`, attaches `termination_reason`. The Session Manager polls this table and disconnects the client on its next tick (typically within 10 seconds).

**Confirmation template.** `TERMINATE <session_id>` typed verbatim into the dashboard.

**Operator-side mistake (terminated wrong session).** There is no undo. The user's transcription state up to the termination is preserved (already in S3); their websocket is closed and the in-flight audio buffer since the last segment boundary is lost. Email the user, document the mistake in the incident channel, learn the session-id ergonomics for next time.

### `force-fail-ingestion` - Stuck or abusive ingestion record

**Symptoms.**

- An upload sits in `pending` or `uploaded` indefinitely (the Step Functions chunker did not pick it up, or the GPU Batch job repeatedly OOMs and retries).
- A tenant uploaded a file that should not be processed (legal hold, abuse report, the wrong tenant's content uploaded to the wrong account).
- Operator wants to disown a specific upload from the pipeline without waiting for the natural expiry.

**Effect.** Marks the row in `panakoes-dev-ingestion` as `failed`, sets `failed_at`, attaches `failure_reason`, sets `failure_source = "tier3.force-fail-ingestion"` so post-incident reconciliation distinguishes operator-driven failures from pipeline-driven failures.

**Confirmation template.** `FAIL <ingestion_id>` typed verbatim into the dashboard.

**Operator-side mistake (failed wrong ingestion).** No undo. The user's upload is now marked `failed` and will not be processed. The S3 object is still in the upload bucket and will age out per the bucket lifecycle policy; the user can re-upload. Email the user; do not try to "recover" the row by editing DynamoDB directly.

### Future Tier 3 operations (Phase 3.2)

The same pattern extends to: `block-user-sessions` (fan-out: terminate all live sessions for a user; partial-completion semantics, see Section 4), `revoke-api-credential`, `force-password-reset`, `rotate-jwt-signing-key`, `drain-streaming-pool`. Each gets its own dashboard panel + confirmation template; the safety substrate is reused.

---

## Section 2 - How to use Tier 3

### Prerequisites

- **Admin role.** Your Better-Auth account has the `admin` role attached. Check via the auth service: `GET /v1/auth/me`; the response should include `roles: ["admin", ...]`.
- **Recent step-up MFA.** Your JWT carries an `mfa_step_up_at` claim within the last 5 minutes (configurable via `STEP_UP_MAX_AGE_SECONDS`). The dashboard prompts for a fresh second-factor challenge if your token is stale; you can pre-warm by clicking "Re-verify" in the user menu before opening Tier 3.
- **Browser session signed into the dashboard.** Tier 3 operations cannot be fired from a curl command without a valid step-up-fresh JWT; the gate is enforced server-side regardless of client.

### Operator workflow

1. Open the admin dashboard (`https://admin.<env>.panakoes.com`) and sign in if not already.
2. Pre-warm step-up MFA: user menu, "Re-verify identity," complete the second factor. Your token now has a fresh `mfa_step_up_at` claim.
3. Navigate to `Lifecycle` in the left nav.
4. Pick the operation panel (`Terminate session`, `Force-fail ingestion`, etc.).
5. Type the target id into the target field. The dashboard renders the expected confirmation string (e.g., `TERMINATE sess_abc123`) below the field.
6. Type the **exact** confirmation string into the confirmation field. Copy-paste from the rendered hint is fine; copy-paste from another browser tab or another session's panel is not (the string includes the target, so a stale paste fails the gate).
7. Type the reason (operationally meaningful: "support ticket #4521, account compromise reported"; "incident #2026-05-09-stuck-ingestion"). The reason is recorded in the audit row.
8. Click `Fire`. The dashboard mints an `idempotency_key` (UUIDv4) and `POST`s to the operation's route.
9. Watch the response panel:
   - Spinner → success card (`status: succeeded`, `result: ...`): operation ran, side effect applied.
   - Spinner → failure card (`status: failed`, `error_message: ...`): protocol ran, handler raised. Read `error_message` and consult Section 4.
   - Red error banner (`HTTP 4xx`): pre-flight gate rejected the request. Read the banner's `detail` field and consult Section 4.

The dashboard renders the response envelope verbatim (status, result or error_message, timestamps, audit_request_id) so the operator can copy fields directly into the incident channel without translating from a generic toast.

---

## Section 3 - Reading the audit log

Every Tier 3 operation (gate-passing, regardless of outcome) writes two rows to `panakoes-dev-audit-log`:

```
intent  row : action="tier3.<op>.intent",  outcome="pending"
outcome row : action="tier3.<op>.outcome", outcome="success"|"failure"
```

Both rows share a single `request_id` (a UUIDv4 minted by `audit_lifecycle`) and both carry the `tier3_action` attribute that backs the `Tier3ActionIndex` GSI.

### Finding Tier 3 actions

Query the GSI by `tier3_action` to retrieve every row (intent + outcome) for a given operation across all actors and time:

```bash
aws dynamodb query \
  --table-name panakoes-dev-audit-log \
  --index-name Tier3ActionIndex \
  --key-condition-expression "tier3_action = :op" \
  --expression-attribute-values '{":op":{"S":"terminate-session"}}' \
  --region us-east-1
```

To narrow to a single incident, filter by `request_id` after the GSI fetch (the GSI is partitioned by `tier3_action`; `request_id` is not an index key, so client-side filter is the right tool):

```bash
aws dynamodb query \
  --table-name panakoes-dev-audit-log \
  --index-name Tier3ActionIndex \
  --key-condition-expression "tier3_action = :op" \
  --filter-expression "request_id = :rid" \
  --expression-attribute-values '{":op":{"S":"terminate-session"},":rid":{"S":"<request_id>"}}' \
  --region us-east-1
```

A correctly completed operation returns two items: one with `action` ending in `.intent`, one with `action` ending in `.outcome`. They share `request_id`, `actor_id`, `tier3_action`, and `target` (a structured map of operation-specific identifiers).

### Per-row column reference

| Column | Type | Meaning |
|---|---|---|
| `pk` | String | Audit-log partition (e.g., `TIER3#<actor_id>`) |
| `sk` | String | Sort key encoding timestamp + request_id + row-kind ordering |
| `action` | String | `tier3.<op>.intent` or `tier3.<op>.outcome` |
| `tier3_action` | String | The operation name (`terminate-session`, `force-fail-ingestion`, etc.); GSI hash key |
| `actor_id` | String | The Better-Auth subject (`sub` claim) of the operator who fired |
| `request_id` | String | UUIDv4 joining the intent + outcome rows |
| `target` | Map | Operation-specific target identifiers (e.g., `{"session_id": "sess_abc"}`) |
| `reason` | String | Operator-supplied free text (intent row only) |
| `outcome` | String | `pending` (intent row), `success` or `failure` (outcome row) |
| `error_type` | String | The exception class name (outcome row, failure case only) |
| `error_message` | String | The exception message (outcome row, failure case only) |
| `timestamp` | String | ISO 8601 UTC; intent row = start, outcome row = finish |

### Spotting a partial failure

A residual `intent` row with no matching `outcome` row is the forensic signal of a hard process failure (container OOM, kernel panic, audit-log write failure during the outcome phase). To detect:

```bash
# Pull every Tier 3 row for an op, group by request_id, find singletons.
aws dynamodb query --table-name panakoes-dev-audit-log \
  --index-name Tier3ActionIndex \
  --key-condition-expression "tier3_action = :op" \
  --expression-attribute-values '{":op":{"S":"terminate-session"}}' \
  --region us-east-1 \
  | jq -r '.Items[] | [.request_id.S, .action.S] | @tsv' \
  | sort | awk '{c[$1]++} END {for (r in c) if (c[r]==1) print r}'
```

A singleton `request_id` with `action` ending in `.intent` and no `.outcome` row means: the operation started, the side effect *may* have applied, the outcome was never recorded. Investigate the corresponding `lifecycle-state` row (key by `idempotency_key`); if it is still `PENDING`, the handler did not finish; if it is `FAILED` or `SUCCEEDED`, the audit-log outcome write is the failure point and not the operation itself.

---

## Section 4 - Common failure modes and recovery

Each row maps a response shape to an operator action.

### `401 Unauthorized` ("authentication required" or "invalid token")

Your JWT is missing, expired, or invalid. Re-sign-in to the dashboard. The lifecycle protocol never started; nothing audited.

### `403 Forbidden` with `detail: "step-up MFA required"` or `"step-up MFA stale"`

Your role is fine; your step-up MFA freshness window has expired. Open the user menu, click "Re-verify identity," complete the second factor. Your `mfa_step_up_at` claim refreshes; retry the operation. Default freshness window is 5 minutes (`STEP_UP_MAX_AGE_SECONDS`).

### `403 Forbidden` with `detail: "admin role required"`

Your account does not have the `admin` role. Stop. This is not your operation to run. Hand off to an operator who does.

### `400 Bad Request` with `detail: "confirmation mismatch: expected '<EXPECTED>'"`

You typed the confirmation string wrong, or you typed it for a different target. Re-read the rendered hint in the dashboard; it includes the exact expected string with the target id baked in. Type it character-for-character. **This is the safety pattern working as designed**; if you find yourself fighting it, slow down and verify you have the right target id before retrying.

### `400 Bad Request` with `detail: "validation error..."` (Pydantic)

The request body is malformed (missing `idempotency_key`, missing required `params.reason`, wrong type). Read the `detail` field's path. The dashboard should not produce these in normal operation; if it does, file a bug.

### `200 OK` with `status: "failed"` and `error_message` populated

Pre-flight gates passed; the handler raised. Both audit rows are written; the `lifecycle-state` row is finalized to `FAILED`; replaying the same `idempotency_key` returns this same envelope (per [ADR-033](../adr/ADR-033-tier-3-response-code-semantics.md)).

Per-operation interpretation:

- **`terminate-session` with `error_message` containing `SessionNotFoundError`.** The session id you targeted does not exist. Either it was already terminated (check the streaming-sessions table) or you typed the wrong id (check the source you copied it from).
- **`force-fail-ingestion` with `error_message` containing `IngestionRecordNotFoundError`.** Same shape: the ingestion id does not exist. Check the ingestion table directly.
- **Future `block-user-sessions` partial completion.** When this operation lands (Phase 3.2), it fans out across N session rows for the target user. Partial completion is possible: some sessions terminated, others raised mid-fan-out. The handler's contract is to terminate every session it can, accumulate per-session results in `result.session_results`, and raise only if zero terminated. A `status: "failed"` envelope with `result.session_results` populated means SOME sessions were terminated; read the array to learn which. Re-firing under a fresh `idempotency_key` re-attempts the un-terminated ones.

To retry a failed operation, mint a **fresh `idempotency_key`** (the dashboard does this automatically on re-fire). Re-using the original key returns the cached failed envelope without running the handler.

### `5xx` (Internal Server Error, Bad Gateway, Service Unavailable)

Infrastructure failure: DynamoDB throttle on `lifecycle-state` or `audit-log`, ECS task crash mid-request, ALB health-check transient, audit-log write failure. The protocol gate prevented the operation from running; the side effect did NOT apply.

Retry safe (the operation did not run). Use the same `idempotency_key` for the retry: if a partial side effect did somehow occur (rare, but possible if the audit-log write succeeded and the lifecycle-state write failed), the cached row collapses the retry to a no-op.

If 5xx repeats, escalate: check `services/admin-api` CloudWatch logs for the actual exception, check DynamoDB throttle metrics on `panakoes-dev-lifecycle-state` and `panakoes-dev-audit-log`, check ECS service event log.

---

## Section 5 - Adding a new lifecycle operation

The safety substrate is built to be extended mechanically. Adding a new Tier 3 operation is mostly typing.

### The recipe

1. **Define the typed parameter and result models** in `services/admin-api/src/panakoes_admin_api/models.py`. The request envelope is the shared `LifecycleRequest` (carries `idempotency_key`, `confirmation`, `params`); the operation declares the typed shape of `params` and `result` for its own validation and tests.

2. **Write the operation handler** in `services/admin-api/src/panakoes_admin_api/operations/<op_name>.py`. Pattern: a `make_handler` factory that captures dependencies (table handles, target id) by closure and returns an async `handler(params: dict) -> dict`. The handler does the side effect and returns a result dict. Raise a typed exception class (e.g., `SessionNotFoundError`) for expected failure modes; the orchestrator catches every exception and finalizes the envelope to `FAILED` with `error_message = f"{type(exc).__name__}: {exc}"`.

   See `services/admin-api/src/panakoes_admin_api/operations/terminate_session.py` and `force_fail_ingestion.py` for concrete examples.

3. **Add the route shim** in `services/admin-api/src/panakoes_admin_api/routes/lifecycle.py`. Pattern: declare the path, wire the dependencies (`require_admin_with_step_up`, `get_audit_table`, `get_lifecycle_state`, the operation-specific table dep), build the handler via `make_handler(...)`, delegate to `execute_lifecycle_or_failed_envelope` with the operation name and the expected-confirmation template (e.g., `f"BLOCK {user_id}"`).

4. **Pick the confirmation template carefully.** It must include the target identifier so a stale copy-paste from a different operation fails the gate. Convention: `<UPPERCASE-VERB> <target_id>`. Examples: `TERMINATE sess_abc`, `FAIL ing_xyz`, `BLOCK usr_123`, `REVOKE cred_456`. Document the template in the route docstring.

5. **Write the audit-log assertions** in `services/admin-api/tests/integration/test_<op_name>_lifecycle.py`. Required assertions per the existing patterns:
   - Pre-flight gate failures (wrong confirmation, missing step-up, wrong role) return the right 4xx and write **zero** audit rows.
   - Successful operation writes exactly two audit rows (intent + outcome with `outcome="success"`), both with the same `request_id`, both indexed under `Tier3ActionIndex`.
   - Handler-raised failure writes exactly two audit rows (intent + outcome with `outcome="failure"` and the exception type captured).
   - Idempotent replay (same `idempotency_key`) returns the cached envelope without re-running the handler and without writing additional audit rows.

6. **Land it behind the dashboard panel** in `services/admin/src/lib/lifecycle/<op-name>/`. The Svelte panel renders the target field, the confirmation hint, the reason field, and the response envelope. Pattern: copy the existing `terminate-session/` panel, swap the operation-specific bits.

7. **Update the runbook.** Add a "When to use" entry to Section 1; add per-operation interpretation to Section 4 if the failure modes are operationally distinctive (most are not).

### What you do NOT need to add

- Step-up MFA gate, idempotency-by-key, audit-row pair writing, the 4xx-vs-200 response-code split, the cached-envelope-on-replay path. All inherited from the substrate.

### Cross-references

- [ADR-032](../adr/ADR-032-tier-3-lifecycle-safety-pattern.md) (the substrate's design)
- [ADR-033](../adr/ADR-033-tier-3-response-code-semantics.md) (the response-code contract you inherit)

---

## Section 6 - Limits

Operational bounds the operator should know about and not be surprised by.

- **`lifecycle-state` TTL: 24h.** Rows in `panakoes-dev-lifecycle-state` carry `expires_at = created_at + 24h`. After expiry, replaying the same `idempotency_key` returns no cached row; the orchestrator treats it as a fresh operation and the handler runs again. 24h is well past any retry-storm horizon and keeps the table small. Forensic record beyond 24h lives in the audit log.

- **Audit-log retention: forever.** `panakoes-dev-audit-log` retains every row indefinitely in DynamoDB, with DynamoDB Streams shipping rows to S3 archive (Glacier-class lifecycle for rows older than 90 days, queryable via Athena). Tier 3 forensic queries beyond the hot DynamoDB window run against the S3 archive.

- **Step-up MFA freshness window: 5 minutes.** Configurable via the `STEP_UP_MAX_AGE_SECONDS` environment variable on `services/admin-api`. The window is uniform across all Tier 3 operations. Per-operation windows (different freshness for `drain` vs `rotate-jwt-signing-key`) are out of scope for v0.1; the substrate supports them if the threat model later separates lower-stakes from higher-stakes operations.

- **Tier3ActionIndex GSI propagation: eventually consistent.** Newly written audit rows take typically less than 1 second to appear under `Tier3ActionIndex`. If you query the GSI immediately after firing an operation, you may see only the intent row, not yet the outcome row. Re-query after a few seconds, or query the base table directly by partition + sort key.

- **`idempotency_key` collision risk: vanishingly small.** Caller-minted UUIDv4. A genuinely fresh operation accidentally re-using a 24h-old key would collapse to the cached envelope without running. The dashboard mints a fresh UUID per fire; manual API callers must ensure uniqueness.

- **Concurrent fires of the same operation against the same target: serialized by `lifecycle-state`.** The `put_pending` write is conditional on key absence. The first fire wins and becomes the canonical row; the second fire's `get` returns the first's `PENDING` envelope without invoking the handler. The second client polls until the first finishes (the dashboard handles this transparently).

---

## Related documents

- [ADR-032: Tier 3 lifecycle safety pattern](../adr/ADR-032-tier-3-lifecycle-safety-pattern.md)
- [ADR-033: Tier 3 lifecycle response-code semantics](../adr/ADR-033-tier-3-response-code-semantics.md)
- [`docs/operations/ci.md`](ci.md) - sibling runbook for CI/CD operations
- [`docs/runbooks/incident-response.md`](../runbooks/incident-response.md) - the broader incident playbook Tier 3 plugs into
- `services/admin-api/README.md` - service-level docs for admin-api
- `services/admin-api/src/panakoes_admin_api/safety.py` - the orchestrator
- `services/admin-api/src/panakoes_admin_api/audit.py` - the `audit_lifecycle` context manager
- `services/admin-api/src/panakoes_admin_api/lifecycle_state.py` - the state store
