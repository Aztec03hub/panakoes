/**
 * Frontend-side mirror of the Pydantic models in `panakoes-models` (Python).
 *
 * This file is intentionally hand-maintained for now; once the panakoes-models
 * Python package gains a JSON Schema export, we will codegen these types from
 * that schema (see `pydantic-to-typescript` or `datamodel-code-generator`)
 * to remove drift risk.
 *
 * Why hand-maintained today: the models repo doesn't exist yet. We want the
 * admin skeleton to compile and test before the canonical schema lands so the
 * dashboard isn't blocked. Drift risk is acceptable in v0.1 because the API
 * contracts are still hardening.
 */

/** Health classification for a single service. */
export type HealthStatus = "healthy" | "unhealthy" | "unknown";

/** ISO-8601 UTC timestamp string (e.g. "2026-05-08T12:34:56.789Z"). */
export type IsoTimestamp = string;

/** A single Panakoes microservice's current health, as reported by the
 * health-aggregator. The aggregator does not exist yet (slice 4 backlog);
 * the current dashboard reads `static/dashboard/health.json` mock data. */
export interface ServiceHealth {
  /** Stable identifier matching the directory name under `services/`. */
  service: string;
  /** Display-friendly name for the UI. */
  display_name: string;
  /** Current rolled-up status. */
  status: HealthStatus;
  /** When the aggregator last received a successful probe. */
  last_check: IsoTimestamp;
  /** Optional message surfaced when status is `unhealthy` or `unknown`. */
  message?: string;
}

/** Snapshot of all monitored services at a given instant. */
export interface HealthSnapshot {
  /** When the snapshot itself was assembled. */
  generated_at: IsoTimestamp;
  /** Health entries, one per monitored service. */
  services: ServiceHealth[];
}

/** Mocked log entry used on the service detail page. */
export interface LogEntry {
  timestamp: IsoTimestamp;
  level: "DEBUG" | "INFO" | "WARN" | "ERROR";
  message: string;
}

/** Mocked error entry used on the service detail page. */
export interface ErrorEntry {
  timestamp: IsoTimestamp;
  message: string;
  count: number;
}

/** Mocked CPU/memory metric snapshot. */
export interface MetricSnapshot {
  cpu_percent: number;
  memory_mb: number;
  memory_limit_mb: number;
}

/** Aggregated detail payload for a single service's drilldown view. */
export interface ServiceDetail {
  service: string;
  display_name: string;
  health: ServiceHealth;
  recent_logs: LogEntry[];
  recent_errors: ErrorEntry[];
  metrics: MetricSnapshot;
}

// ---------------------------------------------------------------------------
// Tier 2 (cost-api) types
//
// Mirror the Pydantic shapes the cost-api service will return in Phase 1+.
// Phase 0 placeholders consume these types so once the real endpoints land,
// only the fetch logic changes; the components stay typed end-to-end.
// ---------------------------------------------------------------------------

/** One row of by-service cost breakdown.
 *
 *  Costs are integer cents to avoid floating-point precision drift. The
 *  display layer formats back to a dollar string at the last possible
 *  moment. Mirrors `panakoes_cost_api.models.CostByService`. */
export interface ServiceCostRow {
  service: string;
  cost_cents: number;
  percent_of_total: number;
}

/** Response shape for `GET /api/v1/cost/by-service`.
 *
 *  Mirrors `panakoes_cost_api.models.CostBreakdown`. The `cache_hit` field
 *  is the operational signal the dashboard surfaces so a human can tell at
 *  a glance whether the response came from DynamoDB cache or paid a CE
 *  round trip. `queried_at` is the wall-clock instant the response was
 *  assembled (server-trusted). */
export interface ServiceCostBreakdown {
  from_date: string;
  to_date: string;
  currency: string;
  services: ServiceCostRow[];
  total_cents: number;
  cache_hit: boolean;
  queried_at: IsoTimestamp;
}

/** One row of by-tenant cost breakdown.
 *
 * Money is integer cents (matches the Pydantic `TenantCostRow` model in
 * `services/cost-api/src/panakoes_cost_api/models.py`). The frontend
 * formats cents into a display string at the last possible moment.
 */
export interface TenantCostRow {
  tenant_id: string;
  display_name: string;
  cost_cents: number;
  percent_of_total: number;
}

/** Response shape for `GET /api/v1/cost/by-tenant`.
 *
 * Mirrors the Pydantic `TenantCostBreakdown` envelope: integer-cents
 * total, ISO date window, `cache_hit` operational flag, server-trusted
 * `queried_at` instant. Field names align 1:1 with the Pydantic model
 * so the JSON parser is the only translation step.
 */
export interface TenantCostBreakdown {
  from_date: string;
  to_date: string;
  currency: string;
  tenants: TenantCostRow[];
  total_cents: number;
  cache_hit: boolean;
  queried_at: IsoTimestamp;
}

/** A single forecast bucket, day-granular. Phase 2 surface. */
export interface ForecastBucket {
  date: string;
  predicted_cost_cents: number;
  lower_bound_cents: number;
  upper_bound_cents: number;
}

/** Response shape for `GET /api/v1/cost/forecast`. Phase 2 surface. */
export interface CostForecast {
  horizon_days: number;
  buckets: ForecastBucket[];
  model: string;
  queried_at: IsoTimestamp;
}

/** A single anomaly entry. Phase 2 surface. */
export interface CostAnomaly {
  signature: string;
  detector: string;
  tenant_id?: string | null;
  dimension_key: string;
  observed_cost_cents: number;
  expected_cost_cents: number;
  deviation_pct: number;
  first_seen: IsoTimestamp;
  last_seen: IsoTimestamp;
  suppressed: boolean;
}

/** Response shape for `GET /api/v1/cost/anomalies`.
 *
 * Mirrors the Pydantic `CostAnomalyList` envelope. `queried_at` is the
 * server-trusted instant the response was assembled.
 */
export interface CostAnomalyList {
  anomalies: CostAnomaly[];
  queried_at: IsoTimestamp;
}

// ---------------------------------------------------------------------------
// Tier 3 (admin-api) types
//
// Mirror the Pydantic shapes for the lifecycle safety pattern. Every
// dangerous operation routes through the same envelope: a typed-confirmation
// string, an idempotency key the caller mints, and a result envelope the
// admin-api writes to `panakoes-dev-lifecycle-state` for at-most-once
// semantics. The audit log entry is populated both before and after the
// operation runs so a partial failure is forensically reconstructible.
// ---------------------------------------------------------------------------

/** Status of a Tier 3 lifecycle operation. */
export type LifecycleStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

/** Request envelope every Tier 3 operation accepts. */
export interface LifecycleRequest<P> {
  /** UUID minted by the caller. Repeated submissions collapse to one effect. */
  idempotency_key: string;
  /** Typed confirmation string the user must type into the UI before submit. */
  confirmation: string;
  /** Operation-specific payload. */
  params: P;
}

/** Response envelope every Tier 3 operation returns. */
export interface LifecycleResponse<R> {
  idempotency_key: string;
  status: LifecycleStatus;
  /** Operation-specific result payload. Present once status is succeeded. */
  result?: R;
  /** Human-readable error message. Present once status is failed. */
  error_message?: string;
  /** Audit-log request id. Joins this operation to its audit row(s). */
  audit_request_id: string;
  started_at: IsoTimestamp;
  finished_at?: IsoTimestamp;
}

/** Tier 3.1 op: terminate a live streaming session. */
export interface TerminateSessionParams {
  session_id: string;
  reason: string;
}

/** Tier 3.1 op: revoke an issued API credential. */
export interface RevokeCredentialParams {
  credential_id: string;
  reason: string;
}

/** Tier 3.1 op: force a password reset for a user. */
export interface ForcePasswordResetParams {
  user_id: string;
  reason: string;
}

/** Tier 3.1 op: force-fail a stuck or abusive ingestion record. */
export interface ForceFailIngestionParams {
  reason: string;
}

/** Tier 3.1 op: block all live sessions for a user. */
export interface BlockUserSessionsParams {
  reason: string;
}

/** Result payload for `terminate-session`. */
export interface TerminateSessionResult {
  session_id: string;
  status: string;
  terminated_at?: IsoTimestamp;
}

/** Result payload for `force-fail-ingestion`. */
export interface ForceFailIngestionResult {
  ingestion_id: string;
  status: string;
  failed_at?: IsoTimestamp;
}

/** Result payload for `block-user-sessions`. */
export interface BlockUserSessionsResult {
  user_id: string;
  affected_count: number;
  blocked_session_ids: string[];
  skipped_count: number;
  noop: boolean;
}

/**
 * Discriminator for the lifecycle dashboard's op selector. The string values
 * match the `op_name` admin-api uses when writing audit rows so the operator
 * can pivot from a UI selection to an audit-log filter without translation.
 */
export type LifecycleOperationKind =
  | "terminate-session"
  | "force-fail-ingestion"
  | "block-user-sessions";

/** Single audit-log row surfaced by the Tier 3.3 read view. */
export interface AuditLogEntry {
  request_id: string;
  timestamp: IsoTimestamp;
  source_service: string;
  actor_id: string;
  action: string;
  /** Tier 3 lifecycle action (sparse; absent on non-Tier-3 events). */
  tier3_action?: string;
  /** Free-form structured payload from the audit producer. */
  payload: Record<string, unknown>;
}

/** Response shape for `GET /admin/audit-log` (paginated, filterable). */
export interface AuditLogPage {
  entries: AuditLogEntry[];
  /** Opaque cursor for the next page; null when this is the last page. */
  next_cursor: string | null;
  generated_at: IsoTimestamp;
}
