import { describe, expect, it, vi } from "vitest";
import { ApiError, COST_API_BASE, fetchCostByService, formatUsdCents } from "../src/lib/api";
import type { ServiceCostBreakdown } from "../src/lib/types";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

const sampleBreakdown: ServiceCostBreakdown = {
  from_date: "2026-04-01",
  to_date: "2026-05-01",
  currency: "USD",
  services: [
    { service: "Amazon EC2", cost_cents: 12345, percent_of_total: 80.0 },
    { service: "Amazon S3", cost_cents: 3000, percent_of_total: 20.0 },
  ],
  total_cents: 15345,
  cache_hit: false,
  queried_at: "2026-04-30T14:32:18Z",
};

describe("fetchCostByService", () => {
  it("returns the parsed typed response on 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleBreakdown));
    const result = await fetchCostByService("2026-04-01", "2026-05-01", fetcher);
    expect(result).toEqual(sampleBreakdown);
    expect(fetcher).toHaveBeenCalledWith(
      `${COST_API_BASE}/v1/cost/by-service?from=2026-04-01&to=2026-05-01`,
      { headers: { Accept: "application/json" } },
    );
  });

  it("URL-encodes the date params (defensive against an inverted call site)", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleBreakdown));
    await fetchCostByService("2026/04/01", "2026/05/01", fetcher);
    const url = fetcher.mock.calls[0]?.[0] as string;
    expect(url).toContain("from=2026%2F04%2F01");
    expect(url).toContain("to=2026%2F05%2F01");
  });

  it("throws ApiError(401) when the endpoint returns 401 (caller redirects)", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(401));
    await expect(fetchCostByService("2026-04-01", "2026-05-01", fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
  });

  it("throws ApiError(502) on upstream Cost Explorer failure", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(502));
    await expect(fetchCostByService("2026-04-01", "2026-05-01", fetcher)).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it("uses an injected base URL for testing / preview environments", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleBreakdown));
    await fetchCostByService("2026-04-01", "2026-05-01", fetcher, "/preview-api");
    expect(fetcher).toHaveBeenCalledWith(
      "/preview-api/v1/cost/by-service?from=2026-04-01&to=2026-05-01",
      { headers: { Accept: "application/json" } },
    );
  });
});

describe("formatUsdCents", () => {
  it("formats integer cents into a USD dollar string with two decimals", () => {
    expect(formatUsdCents(1234)).toBe("$12.34");
    expect(formatUsdCents(0)).toBe("$0.00");
    expect(formatUsdCents(100000)).toBe("$1,000.00");
  });

  it("rounds half-cents at the toLocaleString boundary deterministically", () => {
    // 12.345 dollars -> 1234.5 cents in real life; we never get fractional
    // cents because the API returns ints. Sanity test that the helper does
    // not silently introduce floating-point drift on valid inputs.
    expect(formatUsdCents(1234500)).toBe("$12,345.00");
  });
});
