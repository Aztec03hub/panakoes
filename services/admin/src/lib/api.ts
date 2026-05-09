/**
 * Typed fetch wrappers for the Panakoes admin APIs.
 *
 * Today this file targets a static mock at `/dashboard/health.json` (served
 * from `static/dashboard/health.json` by SvelteKit's static asset pipeline).
 * Once the health-aggregator service ships, swap the URL for the production
 * endpoint and add an Authorization header from `lib/auth.ts`.
 */

import type {
  AuditLogPage,
  BlockUserSessionsParams,
  BlockUserSessionsResult,
  ForceFailIngestionParams,
  ForceFailIngestionResult,
  HealthSnapshot,
  LifecycleRequest,
  LifecycleResponse,
  ServiceDetail,
  TenantCostBreakdown,
  TerminateSessionParams,
  TerminateSessionResult,
} from "./types";

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

// ---------------------------------------------------------------------------
// Tier 2.1 by-tenant cost view
// ---------------------------------------------------------------------------

/** Default endpoint for the by-tenant cost breakdown. */
export const COST_BY_TENANT_ENDPOINT = "/api/v1/cost/by-tenant";

/**
 * Pulls the per-tenant cost breakdown for a date window.
 *
 * Mirrors the by-service helper (which lands in Phase 1.4). The route
 * is admin-gated: callers must already hold an admin JWT in the auth
 * cookie (the Bearer token is attached by the SvelteKit fetch layer
 * once the auth wiring lands; today we trust the dev proxy).
 *
 * `from_date` and `to_date` are ISO-8601 dates (`YYYY-MM-DD`).
 * `to_date` is exclusive on the cost-api side, matching CE semantics.
 *
 * Throws `ApiError` on non-2xx so the dashboard surfaces the error UI
 * via `+error.svelte`.
 */
export async function fetchCostByTenant(
  fromDate: string,
  toDate: string,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  endpoint: string = COST_BY_TENANT_ENDPOINT,
): Promise<TenantCostBreakdown> {
  const url = `${endpoint}?from=${encodeURIComponent(fromDate)}&to=${encodeURIComponent(toDate)}`;
  const response = await fetcher(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch by-tenant cost breakdown (HTTP ${response.status})`,
      response.status,
      url,
    );
  }
  return (await response.json()) as TenantCostBreakdown;
}

// ---------------------------------------------------------------------------
// Tier 3.1 lifecycle operations
//
// Three thin POST helpers, one per Tier 3 operation wired in Phase 3.1. Each
// helper:
//   - URL-encodes the id segment so a path-traversal-shaped id can't escape
//     the route.
//   - Sends the typed `LifecycleRequest<P>` JSON body verbatim. The caller
//     (page.svelte) is responsible for minting the idempotency key and
//     building the typed-confirmation string.
//   - Throws `ApiError` on non-2xx so the page can render the error envelope
//     uniformly. Note: admin-api returns a `LifecycleResponse` envelope with
//     `status: "failed"` for safety-pattern rejections (e.g. confirmation
//     mismatch); see ADR-033. Those come back as 200 OK and we surface them
//     via the `status` discriminator on the response, NOT via ApiError.
// ---------------------------------------------------------------------------

/** Default base path for admin-api. Overridable for tests / non-default
 *  deployments. The dev server is expected to proxy `/api/...` to admin-api. */
export const ADMIN_API_BASE = "/api/v1/admin";

async function postLifecycle<P, R>(
  url: string,
  request: LifecycleRequest<P>,
  fetcher: Fetcher,
): Promise<LifecycleResponse<R>> {
  const response = await fetcher(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(request),
  });
  if (!response.ok) {
    throw new ApiError(
      `Lifecycle operation failed (HTTP ${response.status})`,
      response.status,
      url,
    );
  }
  return (await response.json()) as LifecycleResponse<R>;
}

/**
 * Terminate a live streaming session.
 *
 * Endpoint: `POST /api/v1/admin/sessions/{session_id}/terminate`.
 * Confirmation template (validated server-side): `TERMINATE <session_id>`.
 */
export async function terminateSession(
  sessionId: string,
  request: LifecycleRequest<TerminateSessionParams>,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<TerminateSessionResult>> {
  const url = `${baseUrl}/sessions/${encodeURIComponent(sessionId)}/terminate`;
  return postLifecycle<TerminateSessionParams, TerminateSessionResult>(url, request, fetcher);
}

/**
 * Force-fail a stuck or abusive ingestion record.
 *
 * Endpoint: `POST /api/v1/admin/ingestions/{ingestion_id}/force-fail`.
 * Confirmation template: `FAIL <ingestion_id>`.
 */
export async function forceFailIngestion(
  ingestionId: string,
  request: LifecycleRequest<ForceFailIngestionParams>,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<ForceFailIngestionResult>> {
  const url = `${baseUrl}/ingestions/${encodeURIComponent(ingestionId)}/force-fail`;
  return postLifecycle<ForceFailIngestionParams, ForceFailIngestionResult>(url, request, fetcher);
}

/**
 * Block all live sessions for a user.
 *
 * Endpoint: `POST /api/v1/admin/users/{user_id}/block-sessions`.
 * Confirmation template: `BLOCK USER <user_id>`.
 *
 * Result includes `noop: true` if the user had zero live sessions, so the
 * dashboard can surface "nothing to do" without treating it as a failure.
 */
export async function blockUserSessions(
  userId: string,
  request: LifecycleRequest<BlockUserSessionsParams>,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<BlockUserSessionsResult>> {
  const url = `${baseUrl}/users/${encodeURIComponent(userId)}/block-sessions`;
  return postLifecycle<BlockUserSessionsParams, BlockUserSessionsResult>(url, request, fetcher);
}
