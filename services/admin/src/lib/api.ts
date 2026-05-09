/**
 * Typed fetch wrappers for the Panakoes admin APIs.
 *
 * Today this file targets a static mock at `/dashboard/health.json` (served
 * from `static/dashboard/health.json` by SvelteKit's static asset pipeline).
 * Once the health-aggregator service ships, swap the URL for the production
 * endpoint and add an Authorization header from `lib/auth.ts`.
 */

import type { AuditLogPage, HealthSnapshot, ServiceDetail } from "./types";

/** Thrown when an API call fails because the response is non-2xx. */
export class ApiError extends Error {
  public readonly status: number;
  public readonly url: string;

  constructor(message: string, status: number, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

/**
 * `fetch` adapter so tests can inject a mock without monkey-patching globals.
 * Defaults to `globalThis.fetch` in production code paths.
 */
export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/** Default health snapshot endpoint (mock JSON for now). */
export const HEALTH_ENDPOINT = "/dashboard/health.json";

/**
 * Pulls the current health snapshot for all Panakoes services.
 *
 * Throws `ApiError` if the endpoint returns non-2xx, which the dashboard
 * catches and renders as the friendly error UI in `+error.svelte`.
 */
export async function fetchHealth(
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  endpoint: string = HEALTH_ENDPOINT,
): Promise<HealthSnapshot> {
  const response = await fetcher(endpoint, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch health snapshot (HTTP ${response.status})`,
      response.status,
      endpoint,
    );
  }
  return (await response.json()) as HealthSnapshot;
}

/**
 * Pulls the detail payload for one service. Today this is mocked client-side;
 * once the admin-api ships we will hit `/admin/services/<id>/detail`.
 */
export async function fetchServiceDetail(
  service: string,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
): Promise<ServiceDetail> {
  const endpoint = `/dashboard/${service}.json`;
  const response = await fetcher(endpoint, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch service detail for ${service} (HTTP ${response.status})`,
      response.status,
      endpoint,
    );
  }
  return (await response.json()) as ServiceDetail;
}

/**
 * Convenience predicate: should the dashboard mark this snapshot as degraded?
 * Used by the top-nav badge to flash a warning if any service is unhealthy.
 */
export function isSnapshotDegraded(snapshot: HealthSnapshot): boolean {
  return snapshot.services.some((s) => s.status === "unhealthy");
}

// ---------------------------------------------------------------------------
// Tier 3.3 audit-log read view
//
// Backed by `GET /api/v1/admin/audit-log` on admin-api, which queries the
// `Tier3ActionIndex` GSI on `panakoes-dev-audit-log`. The endpoint requires
// the admin role only (no step-up MFA): per ADR-032 the step-up gate is
// reserved for state-changing operations.
// ---------------------------------------------------------------------------

/** Default base path for admin-api in development. Overridable via the
 *  `endpoint` argument so deployment-specific origins (CloudFront, dev box)
 *  can be wired without code changes. */
export const AUDIT_LOG_ENDPOINT = "/api/v1/admin/audit-log";

/** Optional filters / pagination for the audit-log read view. */
export interface AuditLogFilters {
  /** Exact-match filter on the Tier3ActionIndex hash key. */
  tier3_action?: string;
  /** Opaque cursor returned in the previous page's `next_cursor`. */
  cursor?: string;
  /** Maximum entries per page; admin-api caps this at 100. */
  limit?: number;
}

/**
 * Fetch a page of Tier 3 audit-log entries. Pass `filters.cursor` to walk
 * to the next page; the response's `next_cursor` is null on the final page.
 *
 * Throws `ApiError` on non-2xx (including 401/403 if the operator's session
 * isn't admin). The audit-log page renders that as a friendly error.
 */
export async function fetchAuditLog(
  filters: AuditLogFilters = {},
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  endpoint: string = AUDIT_LOG_ENDPOINT,
): Promise<AuditLogPage> {
  const params = new URLSearchParams();
  if (filters.tier3_action !== undefined && filters.tier3_action !== "") {
    params.set("tier3_action", filters.tier3_action);
  }
  if (filters.cursor !== undefined && filters.cursor !== "") {
    params.set("cursor", filters.cursor);
  }
  if (filters.limit !== undefined) {
    params.set("limit", String(filters.limit));
  }
  const query = params.toString();
  const url = query === "" ? endpoint : `${endpoint}?${query}`;
  const response = await fetcher(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(`Failed to fetch audit log (HTTP ${response.status})`, response.status, url);
  }
  return (await response.json()) as AuditLogPage;
}
