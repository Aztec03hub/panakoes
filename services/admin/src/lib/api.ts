/**
 * Typed fetch wrappers for the Panakoes admin APIs.
 *
 * Today this file targets a static mock at `/dashboard/health.json` (served
 * from `static/dashboard/health.json` by SvelteKit's static asset pipeline).
 * Once the health-aggregator service ships, swap the URL for the production
 * endpoint and add an Authorization header from `lib/auth.ts`.
 */

import type { HealthSnapshot, ServiceDetail } from "./types";

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
