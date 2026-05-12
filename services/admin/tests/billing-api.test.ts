import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  BILLING_PORTAL_SESSION_ENDPOINT,
  createBillingPortalSession,
} from "../src/lib/api";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

describe("createBillingPortalSession", () => {
  it("POSTs the return_url to the billing service and returns the parsed url", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(okJson({ url: "https://billing.stripe.com/session/bps_abc" }));

    const result = await createBillingPortalSession("https://panakoes.com/account", fetcher);

    expect(result).toEqual({ url: "https://billing.stripe.com/session/bps_abc" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [calledUrl, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toBe(BILLING_PORTAL_SESSION_ENDPOINT);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Accept: "application/json",
    });
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ return_url: "https://panakoes.com/account" });
  });

  it("uses an injected endpoint when provided", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson({ url: "https://billing.stripe.com/x" }));
    await createBillingPortalSession(
      "https://panakoes.com/account",
      fetcher,
      "/custom/billing/portal-session",
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe("/custom/billing/portal-session");
  });

  it("throws ApiError on non-2xx with status preserved", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(422));
    await expect(
      createBillingPortalSession("https://evil.example.com/x", fetcher),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      url: BILLING_PORTAL_SESSION_ENDPOINT,
    });
  });

  it("the thrown error is an ApiError + Error", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(500));
    try {
      await createBillingPortalSession("https://panakoes.com/account", fetcher);
      expect.fail("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect(err).toBeInstanceOf(Error);
    }
  });
});

describe("BILLING_PORTAL_SESSION_ENDPOINT", () => {
  it("is composed under the /v1/billing gateway prefix", () => {
    // Vite env not set in tests, so API_BASE_URL is "" and the endpoint
    // resolves to a relative path. The shape we care about is the
    // /v1/billing/portal-session suffix.
    expect(BILLING_PORTAL_SESSION_ENDPOINT).toMatch(/\/v1\/billing\/portal-session$/);
  });
});
