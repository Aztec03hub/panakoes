/**
 * Typed fetch wrappers for the Panakoes admin APIs.
 *
 * Endpoint origins are sourced from `lib/config.ts` (which reads
 * `VITE_API_BASE_URL` at build time) so the same bundle can target dev,
 * preview, and prod by swapping a single environment variable at CI bake.
 *
 * Health snapshot + per-service detail still resolve to the bundled
 * static JSON mocks at `/dashboard/*.json` while
 * `USE_LIVE_HEALTH_AGGREGATOR` is false; once the aggregator ships, the
 * flag flips and the helpers swing over to the live endpoint.
 */

import { goto } from "$app/navigation";
import { bearerHeader, currentSession, signOut } from "./auth.svelte";
import {
  API_BASE_URL,
  ADMIN_API_BASE as CONFIG_ADMIN_API_BASE,
  BILLING_API_BASE as CONFIG_BILLING_API_BASE,
  COST_API_BASE as CONFIG_COST_API_BASE,
  USE_LIVE_HEALTH_AGGREGATOR,
} from "./config";
import {
  ATTR_HTTP_METHOD,
  ATTR_HTTP_URL,
  ATTR_USER_ID,
  ATTR_USER_ROLE,
  addAttributes,
  getTracer,
  injectTraceContext,
  recordException,
  setHttpStatus,
} from "./otel";
import type {
  AuditLogPage,
  BlockTenantParams,
  BlockTenantResult,
  BlockUserSessionsParams,
  BlockUserSessionsResult,
  CostAnomalyList,
  CostForecast,
  ForceBillingRecomputeParams,
  ForceBillingRecomputeResult,
  ForceFailIngestionParams,
  ForceFailIngestionResult,
  HealthSnapshot,
  KillBatchJobParams,
  KillBatchJobResult,
  KillStreamingSessionParams,
  KillStreamingSessionResult,
  LifecycleRequest,
  LifecycleResponse,
  RevokeApiKeyParams,
  RevokeApiKeyResult,
  ServiceCostBreakdown,
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

/** Returns true if an error is an `ApiError` representing an auth failure. */
export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 401 || err.status === 403);
}

/**
 * Optional dependencies for `apiFetch` so tests can inject without
 * monkey-patching globals.
 */
export interface ApiFetchDeps {
  fetcher?: Fetcher;
  /** Returns the Authorization header to merge. Defaults to the auth store. */
  authHeader?: () => Record<string, string>;
  /** Called when the server returns 401; defaults to global signOut. */
  onUnauthorized?: () => void;
  /** Called to navigate after sign-out on 401; defaults to SvelteKit `goto`. */
  navigate?: (url: string) => void;
  /** Returns the current pathname for the `?from=` redirect param. */
  currentPath?: () => string;
}

/**
 * The default `Fetcher` used by every helper below when no explicit
 * fetcher is injected (i.e. production code paths). Tests pass their own
 * fetcher mocks so the network never gets touched in unit tests and the
 * 401-side-effects are exercised by the dedicated `apiFetch` tests.
 */
export const defaultFetcher: Fetcher = (input, init) => apiFetch(input, init);

function defaultCurrentPath(): string {
  if (typeof globalThis !== "undefined" && "location" in globalThis) {
    const loc = (globalThis as { location?: { pathname?: string } }).location;
    if (loc?.pathname !== undefined && loc.pathname !== "") {
      return loc.pathname;
    }
  }
  return "/";
}

/**
 * Single chokepoint for every authenticated request out of the SPA.
 *
 * Merges the current session's `Authorization: Bearer <jwt>` header into
 * `init.headers`. On a 401 from any downstream endpoint, clears the local
 * session and redirects to `/login?from=<current>` so the user can sign
 * back in and land where they were.
 *
 * The 401 response is also returned to the caller so existing call-sites
 * that throw `ApiError` continue to surface the failure; `signOut +
 * navigate` is fired in addition to, not instead of, the typed error.
 */
export async function apiFetch(
  url: string,
  init: RequestInit = {},
  deps: ApiFetchDeps = {},
): Promise<Response> {
  const fetcher = deps.fetcher ?? globalThis.fetch.bind(globalThis);
  const authHeader = deps.authHeader ?? bearerHeader;
  const onUnauthorized = deps.onUnauthorized ?? signOut;
  const navigate = deps.navigate ?? ((u: string) => void goto(u));
  const currentPath = deps.currentPath ?? defaultCurrentPath;

  const method = (init.method ?? "GET").toUpperCase();
  const tracer = getTracer();
  return tracer.startActiveSpan(`HTTP ${method} ${url}`, async (span) => {
    try {
      // Build the merged header set (auth + trace context) so the outbound
      // request carries both the bearer + W3C traceparent. Trace context is
      // injected last so any user-supplied traceparent is overridden by the
      // currently active span.
      const baseHeaders: Record<string, string> = {
        ...(init.headers as Record<string, string> | undefined),
        ...authHeader(),
      };
      const headersWithTrace = injectTraceContext(baseHeaders);

      addAttributes(span, {
        [ATTR_HTTP_METHOD]: method,
        [ATTR_HTTP_URL]: url,
      });
      const session = currentSession.value;
      if (session !== null) {
        addAttributes(span, {
          [ATTR_USER_ID]: session.user.id,
          [ATTR_USER_ROLE]: session.user.role,
        });
      }

      const merged: RequestInit = { ...init, headers: headersWithTrace };
      const response = await fetcher(url, merged);
      setHttpStatus(span, response.status);
      if (response.status === 401) {
        onUnauthorized();
        const from = encodeURIComponent(currentPath());
        navigate(`/login?from=${from}`);
      }
      return response;
    } catch (err) {
      recordException(span, err);
      throw err;
    } finally {
      span.end();
    }
  });
}

/**
 * Default health snapshot endpoint.
 *
 * While the `USE_LIVE_HEALTH_AGGREGATOR` flag is false (today's default),
 * this resolves to the bundled static mock under `static/dashboard/`.
 * Once the health-aggregator service ships, flipping the flag points
 * the SPA at `${API_BASE_URL}/v1/health-aggregator/health` instead,
 * with no other code changes required.
 */
export const HEALTH_ENDPOINT = USE_LIVE_HEALTH_AGGREGATOR
  ? `${API_BASE_URL}/v1/health-aggregator/healthz`
  : "/dashboard/health.json";

/**
 * Default per-service detail endpoint pattern.
 *
 * Flag-gated identically to `HEALTH_ENDPOINT`. The aggregator service
 * exposes per-service detail at `/services/<id>` post-mount; until it
 * ships, we keep the static mock under `static/dashboard/<service>.json`.
 */
function serviceDetailEndpoint(service: string): string {
  if (USE_LIVE_HEALTH_AGGREGATOR) {
    return `${API_BASE_URL}/v1/health-aggregator/services/${encodeURIComponent(service)}`;
  }
  return `/dashboard/${service}.json`;
}

/**
 * Pulls the current health snapshot for all Panakoes services.
 *
 * Throws `ApiError` if the endpoint returns non-2xx, which the dashboard
 * catches and renders as the friendly error UI in `+error.svelte`.
 */
export async function fetchHealth(
  fetcher: Fetcher = defaultFetcher,
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
  fetcher: Fetcher = defaultFetcher,
): Promise<ServiceDetail> {
  const endpoint = serviceDetailEndpoint(service);
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
// Tier 2.1 by-service cost view (Phase 1.4)
// ---------------------------------------------------------------------------

/**
 * Default base URL for cost-api. Sourced from `lib/config.ts` so the
 * same bundle can target dev / preview / prod by swapping
 * `VITE_API_BASE_URL` at build time. Overridable for tests / local-stack.
 *
 * Resolves to `${API_BASE_URL}/v1/cost-api/api` per ADR-038's (c+) shape:
 * the gateway forwards `/v1/cost-api/{proxy+}` to the cost-api NLB with
 * the `/v1/cost-api` prefix stripped, so the backend FastAPI router
 * (mounted at `/api/v1/cost`) still receives canonical paths.
 */
export const COST_API_BASE = CONFIG_COST_API_BASE;

/**
 * Fetch the per-service cost breakdown for a date window.
 *
 * Mirrors `GET /api/v1/cost/by-service?from=YYYY-MM-DD&to=YYYY-MM-DD` on
 * the cost-api service. The dashboard renders the cache_hit flag verbatim
 * so a human can tell at a glance whether the response was cached.
 *
 * Throws `ApiError` on any non-2xx response. The caller decides whether
 * to redirect (401) or render an error banner (5xx).
 */
export async function fetchCostByService(
  fromDate: string,
  toDate: string,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = COST_API_BASE,
): Promise<ServiceCostBreakdown> {
  const endpoint =
    `${baseUrl}/v1/cost/by-service?from=${encodeURIComponent(fromDate)}` +
    `&to=${encodeURIComponent(toDate)}`;
  const response = await fetcher(endpoint, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch cost-by-service for ${fromDate}..${toDate} ` + `(HTTP ${response.status})`,
      response.status,
      endpoint,
    );
  }
  return (await response.json()) as ServiceCostBreakdown;
}

/**
 * Format an integer-cents value as a USD dollar string with two decimals.
 *
 * Money is integer cents end-to-end; this helper is the only place the
 * application converts to a display string. Localization is intentionally
 * minimal in v0.1.
 */
export function formatUsdCents(cents: number): string {
  const dollars = cents / 100;
  return `$${dollars.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
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
export const AUDIT_LOG_ENDPOINT = `${CONFIG_ADMIN_API_BASE}/audit-log`;

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
  fetcher: Fetcher = defaultFetcher,
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
export const COST_BY_TENANT_ENDPOINT = `${CONFIG_COST_API_BASE}/v1/cost/by-tenant`;

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
  fetcher: Fetcher = defaultFetcher,
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
// Tier 2.2 cost forecast view
//
// Backed by `GET /api/v1/cost/forecast?horizon_days=N` on cost-api. The
// dashboard renders the per-day predicted spend plus a confidence band
// (lower / upper bounds) sourced from CE's built-in forecasting model.
// Admin role required, same as the by-service / by-tenant routes.
// ---------------------------------------------------------------------------

/** Default endpoint for the cost forecast view. */
export const COST_FORECAST_ENDPOINT = `${CONFIG_COST_API_BASE}/v1/cost/forecast`;

/** Allowed horizon values surfaced in the page's dropdown. Centralized
 *  here so the page and the test agree on the menu without duplication. */
export const COST_FORECAST_HORIZONS = [7, 14, 30, 60, 90] as const;
export type CostForecastHorizon = (typeof COST_FORECAST_HORIZONS)[number];

/**
 * Pulls the cost forecast for a horizon expressed in days.
 *
 * Mirrors `GET /api/v1/cost/forecast?horizon_days=N`. The response shape
 * matches the Pydantic `CostForecast` model: a list of day-granular
 * buckets each carrying `predicted_cost_cents`, `lower_bound_cents`,
 * and `upper_bound_cents`, plus a `model` identifier and the
 * server-trusted `queried_at` instant.
 *
 * Throws `ApiError` on non-2xx so the dashboard surfaces the error UI.
 */
export async function fetchCostForecast(
  horizonDays: number,
  fetcher: Fetcher = defaultFetcher,
  endpoint: string = COST_FORECAST_ENDPOINT,
): Promise<CostForecast> {
  const url = `${endpoint}?horizon_days=${encodeURIComponent(String(horizonDays))}`;
  const response = await fetcher(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch cost forecast (HTTP ${response.status})`,
      response.status,
      url,
    );
  }
  return (await response.json()) as CostForecast;
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

/** Default base path for admin-api. Resolves to
 *  `${API_BASE_URL}/v1/admin-api/api/v1/admin` per ADR-038's (c+) routing
 *  shape (gateway strips `/v1/admin-api`, backend FastAPI mount is
 *  `/api/v1/admin`). Overridable for tests / non-default deployments. */
export const ADMIN_API_BASE = CONFIG_ADMIN_API_BASE;

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
  fetcher: Fetcher = defaultFetcher,
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
  fetcher: Fetcher = defaultFetcher,
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
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<BlockUserSessionsResult>> {
  const url = `${baseUrl}/users/${encodeURIComponent(userId)}/block-sessions`;
  return postLifecycle<BlockUserSessionsParams, BlockUserSessionsResult>(url, request, fetcher);
}

// ---------------------------------------------------------------------------
// Tier 3.2 lifecycle operations (Phase 2: 5 more ops on the proven pattern).
// Each helper mirrors the Phase 1 shape: URL-encoded path segment, JSON body,
// ApiError on non-2xx, protocol-failure-as-200 surfaced via the response
// envelope's `status` discriminator (NOT via ApiError). See ADR-032 + ADR-033.
// ---------------------------------------------------------------------------

/**
 * Block a tenant.
 *
 * Endpoint: `POST /api/v1/admin/tenants/{tenant_id}/block`.
 * Confirmation template: `BLOCK TENANT <tenant_id>`.
 */
export async function blockTenant(
  tenantId: string,
  request: LifecycleRequest<BlockTenantParams>,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<BlockTenantResult>> {
  const url = `${baseUrl}/tenants/${encodeURIComponent(tenantId)}/block`;
  return postLifecycle<BlockTenantParams, BlockTenantResult>(url, request, fetcher);
}

/**
 * Revoke an API key.
 *
 * Endpoint: `POST /api/v1/admin/api-keys/{api_key_id}/revoke`.
 * Confirmation template: `REVOKE KEY <api_key_id>`.
 */
export async function revokeApiKey(
  apiKeyId: string,
  request: LifecycleRequest<RevokeApiKeyParams>,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<RevokeApiKeyResult>> {
  const url = `${baseUrl}/api-keys/${encodeURIComponent(apiKeyId)}/revoke`;
  return postLifecycle<RevokeApiKeyParams, RevokeApiKeyResult>(url, request, fetcher);
}

/**
 * Kill a streaming session: terminate the row AND emit an EventBridge
 * tombstone for the GPU spawner Lambda to decommission the EC2 instance.
 *
 * Endpoint: `POST /api/v1/admin/streaming-sessions/{session_id}/kill`.
 * Confirmation template: `KILL STREAM <session_id>`.
 */
export async function killStreamingSession(
  sessionId: string,
  request: LifecycleRequest<KillStreamingSessionParams>,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<KillStreamingSessionResult>> {
  const url = `${baseUrl}/streaming-sessions/${encodeURIComponent(sessionId)}/kill`;
  return postLifecycle<KillStreamingSessionParams, KillStreamingSessionResult>(
    url,
    request,
    fetcher,
  );
}

/**
 * Kill an AWS Batch job. Reason surfaces in CloudTrail and the Batch console.
 *
 * Endpoint: `POST /api/v1/admin/batch-jobs/{job_id}/kill`.
 * Confirmation template: `KILL JOB <job_id>`.
 */
export async function killBatchJob(
  jobId: string,
  request: LifecycleRequest<KillBatchJobParams>,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<KillBatchJobResult>> {
  const url = `${baseUrl}/batch-jobs/${encodeURIComponent(jobId)}/kill`;
  return postLifecycle<KillBatchJobParams, KillBatchJobResult>(url, request, fetcher);
}

/**
 * Queue a billing recompute for a tenant via EventBridge. Async; the
 * response confirms QUEUED, not COMPLETED.
 *
 * Endpoint: `POST /api/v1/admin/tenants/{tenant_id}/force-billing-recompute`.
 * Confirmation template: `RECOMPUTE BILLING <tenant_id>`.
 */
export async function forceBillingRecompute(
  tenantId: string,
  request: LifecycleRequest<ForceBillingRecomputeParams>,
  fetcher: Fetcher = defaultFetcher,
  baseUrl: string = ADMIN_API_BASE,
): Promise<LifecycleResponse<ForceBillingRecomputeResult>> {
  const url = `${baseUrl}/tenants/${encodeURIComponent(tenantId)}/force-billing-recompute`;
  return postLifecycle<ForceBillingRecomputeParams, ForceBillingRecomputeResult>(
    url,
    request,
    fetcher,
  );
}

// ---------------------------------------------------------------------------
// Tier 2.3 cost anomalies view
//
// Backed by `GET /api/v1/cost/anomalies` on cost-api. The route is admin
// gated; an empty `anomalies` list is the steady-state ("no active
// anomalies") and the dashboard renders it as the healthy detector
// signal rather than as an error.
// ---------------------------------------------------------------------------

/** Default endpoint for the cost-anomaly feed. */
export const COST_ANOMALIES_ENDPOINT = `${CONFIG_COST_API_BASE}/v1/cost/anomalies`;

/**
 * Pulls the current cost-anomaly feed.
 *
 * `detector` narrows the result to one detector name when set;
 * `activeOnly` (default true) skips the Cost Explorer round trip and
 * reads only the dedup-active alert-state rows. Pass `false` to also
 * fetch fresh CE-detected anomalies and surface dedup-suppressed
 * duplicates with `suppressed=true` (useful when investigating a
 * paged anomaly's history).
 *
 * Throws `ApiError` on non-2xx so the dashboard surfaces the error UI.
 */
export async function fetchCostAnomalies(
  detector?: string,
  activeOnly = true,
  fetcher: Fetcher = defaultFetcher,
  endpoint: string = COST_ANOMALIES_ENDPOINT,
): Promise<CostAnomalyList> {
  const params = new URLSearchParams();
  if (detector !== undefined && detector !== "") {
    params.set("detector", detector);
  }
  // The cost-api default is active_only=true, but we send it
  // explicitly so the URL always reflects the caller's intent
  // (mirrors how `fetchAuditLog` encodes its filters).
  params.set("active_only", activeOnly ? "true" : "false");
  const query = params.toString();
  const url = query === "" ? endpoint : `${endpoint}?${query}`;
  const response = await fetcher(url, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to fetch cost anomalies (HTTP ${response.status})`,
      response.status,
      url,
    );
  }
  return (await response.json()) as CostAnomalyList;
}

// ---------------------------------------------------------------------------
// Billing: Stripe Customer Portal session
//
// The Customer Portal is hosted by Stripe. We mint a one-shot session URL
// via `POST /v1/billing/portal-session` and redirect the user to it. The
// portal returns the user to `return_url` when they close it, so we pass
// the current page so they land back where they were.
//
// `return_url` is validated server-side against a Panakoes-owned allowlist
// (cloudfront.net + panakoes.com), so an attacker who forges the body
// still cannot use the endpoint as an open redirect.
// ---------------------------------------------------------------------------

/** Default base path for the billing service under the gateway. */
export const BILLING_API_BASE = CONFIG_BILLING_API_BASE;

/** Portal-session endpoint URL. */
export const BILLING_PORTAL_SESSION_ENDPOINT = `${BILLING_API_BASE}/portal-session`;

/** Response shape from `POST /v1/billing/portal-session`. */
export interface BillingPortalSessionResponse {
  url: string;
}

/**
 * Create a Stripe Customer Portal session for the current user.
 *
 * Posts `{ return_url }` to the billing service and returns the
 * Stripe-hosted portal URL the caller should redirect the browser to.
 * The server validates `return_url` against a Panakoes allowlist;
 * passing an external URL surfaces as `ApiError(status=422)`.
 *
 * Throws `ApiError` on any non-2xx response so the caller can render a
 * friendly error (and `apiFetch`'s 401 handler still fires on auth
 * failure).
 */
export async function createBillingPortalSession(
  returnUrl: string,
  fetcher: Fetcher = defaultFetcher,
  endpoint: string = BILLING_PORTAL_SESSION_ENDPOINT,
): Promise<BillingPortalSessionResponse> {
  const response = await fetcher(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ return_url: returnUrl }),
  });
  if (!response.ok) {
    throw new ApiError(
      `Failed to create billing portal session (HTTP ${response.status})`,
      response.status,
      endpoint,
    );
  }
  return (await response.json()) as BillingPortalSessionResponse;
}
