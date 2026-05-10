import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  COST_FORECAST_ENDPOINT,
  COST_FORECAST_HORIZONS,
  fetchCostForecast,
} from "../src/lib/api";
import type { CostForecast } from "../src/lib/types";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

const sampleForecast: CostForecast = {
  horizon_days: 30,
  buckets: [
    {
      date: "2026-05-11",
      predicted_cost_cents: 1234,
      lower_bound_cents: 1100,
      upper_bound_cents: 1400,
    },
    {
      date: "2026-05-12",
      predicted_cost_cents: 1280,
      lower_bound_cents: 1140,
      upper_bound_cents: 1450,
    },
  ],
  model: "ce-builtin",
  queried_at: "2026-05-10T12:00:00Z",
};

describe("fetchCostForecast", () => {
  it("returns the parsed typed response on 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleForecast));
    const result = await fetchCostForecast(30, fetcher);
    expect(result).toEqual(sampleForecast);
    expect(fetcher).toHaveBeenCalledWith(`${COST_FORECAST_ENDPOINT}?horizon_days=30`, {
      headers: { Accept: "application/json" },
    });
  });

  it("encodes the horizon param defensively", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleForecast));
    await fetchCostForecast(7, fetcher);
    const url = fetcher.mock.calls[0]?.[0] as string;
    expect(url).toContain("horizon_days=7");
  });

  it("throws ApiError(401) when the endpoint returns 401", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(401));
    await expect(fetchCostForecast(30, fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
  });

  it("throws ApiError(502) on upstream Cost Explorer failure", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(502));
    await expect(fetchCostForecast(30, fetcher)).rejects.toBeInstanceOf(ApiError);
  });

  it("uses an injected endpoint for testing / preview environments", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleForecast));
    await fetchCostForecast(30, fetcher, "/preview-api/v1/cost/forecast");
    expect(fetcher).toHaveBeenCalledWith("/preview-api/v1/cost/forecast?horizon_days=30", {
      headers: { Accept: "application/json" },
    });
  });

  it("exposes the supported horizons menu the page binds to", () => {
    expect(COST_FORECAST_HORIZONS).toEqual([7, 14, 30, 60, 90]);
  });
});
