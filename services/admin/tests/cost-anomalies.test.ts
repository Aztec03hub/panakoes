import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  COST_ANOMALIES_ENDPOINT,
  fetchCostAnomalies,
  formatUsdCents,
} from "../src/lib/api";
import type { CostAnomalyList } from "../src/lib/types";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

const sampleList: CostAnomalyList = {
  anomalies: [
    {
      signature: "sig-1",
      detector: "ce-monitor",
      tenant_id: null,
      dimension_key: "Amazon EC2",
      observed_cost_cents: 20000,
      expected_cost_cents: 5000,
      deviation_pct: 300.0,
      first_seen: "2026-05-01T00:00:00Z",
      last_seen: "2026-05-02T00:00:00Z",
      suppressed: false,
    },
  ],
  queried_at: "2026-05-09T00:00:00Z",
};

const emptyList: CostAnomalyList = {
  anomalies: [],
  queried_at: "2026-05-09T00:00:00Z",
};

describe("fetchCostAnomalies", () => {
  it("returns the parsed list on 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleList));
    const result = await fetchCostAnomalies(undefined, true, fetcher);
    expect(result).toEqual(sampleList);
    // The default args still produce a query string with active_only.
    const calledUrl = fetcher.mock.calls[0][0];
    expect(calledUrl).toContain(`${COST_ANOMALIES_ENDPOINT}?`);
    expect(calledUrl).toContain("active_only=true");
  });

  it("encodes detector and active_only=false into the query string", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleList));
    await fetchCostAnomalies("ce-monitor", false, fetcher);
    const calledUrl = fetcher.mock.calls[0][0];
    expect(calledUrl).toContain("detector=ce-monitor");
    expect(calledUrl).toContain("active_only=false");
  });

  it("omits the detector parameter when the filter is empty", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(emptyList));
    await fetchCostAnomalies("", true, fetcher);
    const calledUrl = fetcher.mock.calls[0][0];
    expect(calledUrl).not.toContain("detector=");
    expect(calledUrl).toContain("active_only=true");
  });

  it("throws ApiError on non-2xx with the URL preserved", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(errorResponse(503))
      .mockResolvedValueOnce(errorResponse(503));
    await expect(fetchCostAnomalies(undefined, true, fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
    });
    await expect(fetchCostAnomalies(undefined, true, fetcher)).rejects.toBeInstanceOf(ApiError);
  });

  it("returns an empty list cleanly when the endpoint reports no anomalies", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(emptyList));
    const result = await fetchCostAnomalies(undefined, true, fetcher);
    expect(result.anomalies).toEqual([]);
    expect(result.queried_at).toBe("2026-05-09T00:00:00Z");
  });
});

describe("formatUsdCents", () => {
  it("formats integer cents into a dollar string with two decimal places", () => {
    expect(formatUsdCents(0)).toBe("$0.00");
    expect(formatUsdCents(1234)).toBe("$12.34");
    expect(formatUsdCents(20000)).toBe("$200.00");
  });
});
