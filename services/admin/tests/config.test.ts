import { describe, expect, it } from "vitest";
import {
  ADMIN_API_BASE,
  API_BASE_URL,
  COST_API_BASE,
  USE_LIVE_HEALTH_AGGREGATOR,
} from "../src/lib/config";

// Vitest does not load Vite's `.env.development` (test mode reads
// `.env.test` if present), so `VITE_API_BASE_URL` is unset in the test
// environment and `API_BASE_URL` falls back to the empty string.
//
// That falsy default is the contract: helper URLs built from it look
// identical to the pre-refactor relative paths, which is what every
// existing test asserts. This file pins that contract and the (c+)
// path-composition rules so a future config edit that silently breaks
// either fails loudly here.

describe("config", () => {
  it("defaults API_BASE_URL to the empty string when VITE_API_BASE_URL is unset", () => {
    // The vitest environment does not inject VITE_API_BASE_URL, so the
    // fallback path is exercised on every test run.
    expect(API_BASE_URL).toBe("");
  });

  it("composes COST_API_BASE from API_BASE_URL + the (c+) gateway prefix", () => {
    // ADR-038 (c+): /v1/cost-api/{proxy+} -> cost-api backend with the
    // /v1/cost-api prefix stripped; backend FastAPI mounts at /api/v1/cost.
    // So the composed base is `${API_BASE_URL}/v1/cost-api/api` and the
    // fetch helpers append `/v1/cost/<route>` on top of that.
    expect(COST_API_BASE).toBe(`${API_BASE_URL}/v1/cost-api/api`);
  });

  it("composes ADMIN_API_BASE to the full /v1/admin path under the gateway", () => {
    // admin-api FastAPI mounts at /api/v1/admin; gateway strips
    // /v1/admin-api; so the composed base is the joined path.
    expect(ADMIN_API_BASE).toBe(`${API_BASE_URL}/v1/admin-api/api/v1/admin`);
  });

  it("defaults USE_LIVE_HEALTH_AGGREGATOR to false when the env var is unset", () => {
    expect(USE_LIVE_HEALTH_AGGREGATOR).toBe(false);
  });
});
