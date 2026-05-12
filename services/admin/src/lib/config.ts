/**
 * Build-time configuration surface for the admin SPA.
 *
 * Vite inlines every `import.meta.env.VITE_*` reference at build time, so
 * the values here are baked into the static bundle. The SPA is a public
 * static asset (S3 + CloudFront), so none of these are secrets: they only
 * configure which public origins / public endpoints the dashboard hits.
 *
 * One source of truth, read once at module load. Test code can override
 * by passing explicit endpoint / baseUrl arguments to the API helpers in
 * `lib/api.ts` (the helpers keep their injectable defaults).
 *
 * See `services/admin/.env.example` for the documented contract and
 * deployment notes (CI bake-time injection, prod custom domain timeline).
 */

/** Trim a trailing slash so concatenation with `/v1/...` never doubles up. */
function stripTrailingSlash(url: string): string {
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

/**
 * Parse a Vite env string into a boolean, defaulting to `true`.
 * Used for flags whose default has flipped from off to on; unset env
 * still resolves to the new default.
 */
function envBoolDefaultTrue(value: string | undefined): boolean {
  if (value === undefined || value === "") {
    return true;
  }
  return !(value === "false" || value === "0");
}

/**
 * Origin of the API Gateway in front of cost-api + admin-api + auth.
 *
 * Empty string = relative paths (local dev with Vite proxy, or tests
 * that don't care about the origin). Build-time CI sets this to the
 * deployed gateway URL (today: the raw execute-api host; future: a
 * custom api.panakoes.com domain once Route53 + ACM ship).
 *
 * In production builds we deliberately fall back to empty string rather
 * than throwing, because the SPA is also useful with relative paths
 * when fronted by a CloudFront behavior that proxies /v1/* to the
 * gateway (a future deployment shape that avoids the cross-origin
 * round-trip and CORS preflights entirely).
 */
export const API_BASE_URL: string = stripTrailingSlash(import.meta.env.VITE_API_BASE_URL ?? "");

/**
 * Feature flag: when true (today's default, flipped on once the
 * aggregator shipped), the dashboard fetches health + per-service
 * detail from the live health-aggregator service. When explicitly set
 * to `false` or `0`, it falls back to the bundled static JSON mocks
 * under `static/dashboard/` (useful for offline local dev and for
 * temporarily isolating the SPA from a degraded aggregator).
 *
 * Unset env resolves to `true` since the aggregator is the new
 * canonical source for Tier 1 dashboard data.
 */
export const USE_LIVE_HEALTH_AGGREGATOR: boolean = envBoolDefaultTrue(
  import.meta.env.VITE_USE_LIVE_HEALTH_AGGREGATOR,
);

/**
 * Per-service base paths under the gateway, per ADR-038's (c+) routing
 * shape. The gateway forwards `/v1/<service>/{proxy+}` to the service's
 * NLB with the prefix stripped, so the backend sees its canonical path
 * (e.g. cost-api's FastAPI router still mounts at `/api/v1/cost/`).
 *
 * Putting the gateway-prefix + backend-mount in one place here means
 * every fetch helper composes a URL from a single string, and the c+
 * shape is documented inline next to the constants that depend on it.
 */
export const COST_API_BASE = `${API_BASE_URL}/v1/cost-api/api`;
export const ADMIN_API_BASE = `${API_BASE_URL}/v1/admin-api/api/v1/admin`;
export const AUTH_API_BASE = `${API_BASE_URL}/v1/auth`;
export const BILLING_API_BASE = `${API_BASE_URL}/v1/billing`;
