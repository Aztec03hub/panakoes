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
