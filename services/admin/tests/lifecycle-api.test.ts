import { describe, expect, it, vi } from "vitest";
import {
  ADMIN_API_BASE,
  ApiError,
  blockTenant,
  blockUserSessions,
  forceBillingRecompute,
  forceFailIngestion,
  killBatchJob,
  killStreamingSession,
  revokeApiKey,
  terminateSession,
} from "../src/lib/api";
import type { LifecycleResponse } from "../src/lib/types";
import { generateIdempotencyKey, isUuidV4 } from "../src/lib/uuid";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

const sampleTerminate: LifecycleResponse<{
  session_id: string;
  status: string;
  terminated_at?: string;
}> = {
  idempotency_key: "11111111-1111-4111-8111-111111111111",
  status: "succeeded",
  result: {
    session_id: "sess_abc",
    status: "terminated",
    terminated_at: "2026-05-09T01:02:03Z",
  },
  audit_request_id: "req_001",
  started_at: "2026-05-09T01:02:00Z",
  finished_at: "2026-05-09T01:02:03Z",
};

describe("terminateSession", () => {
  it("posts the request envelope to the encoded session path and parses the response", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleTerminate));
    const result = await terminateSession(
      "sess_abc",
      {
        idempotency_key: sampleTerminate.idempotency_key,
        confirmation: "TERMINATE sess_abc",
        params: { session_id: "sess_abc", reason: "incident-1" },
      },
      fetcher,
    );
    expect(result).toEqual(sampleTerminate);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [calledUrl, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/sessions/sess_abc/terminate`);
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      "Content-Type": "application/json",
      Accept: "application/json",
    });
    const body = JSON.parse(init.body as string);
    expect(body.confirmation).toBe("TERMINATE sess_abc");
    expect(body.params).toEqual({ session_id: "sess_abc", reason: "incident-1" });
  });

  it("URL-encodes path-traversal-shaped session ids", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleTerminate));
    await terminateSession(
      "sess/../../etc",
      {
        idempotency_key: "k",
        confirmation: "TERMINATE sess/../../etc",
        params: { session_id: "sess/../../etc", reason: "test" },
      },
      fetcher,
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/sessions/sess%2F..%2F..%2Fetc/terminate`);
    expect(calledUrl).not.toContain("/../");
  });

  it("throws ApiError on non-2xx with the operation URL preserved", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(401));
    await expect(
      terminateSession(
        "sess_x",
        {
          idempotency_key: "k",
          confirmation: "TERMINATE sess_x",
          params: { session_id: "sess_x", reason: "r" },
        },
        fetcher,
      ),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      url: `${ADMIN_API_BASE}/sessions/sess_x/terminate`,
    });
  });
});

describe("forceFailIngestion", () => {
  it("posts to the encoded ingestion path and returns the parsed envelope", async () => {
    const payload: LifecycleResponse<{
      ingestion_id: string;
      status: string;
      failed_at?: string;
    }> = {
      idempotency_key: "22222222-2222-4222-8222-222222222222",
      status: "succeeded",
      result: { ingestion_id: "ing_xyz", status: "failed", failed_at: "2026-05-09T02:00:00Z" },
      audit_request_id: "req_002",
      started_at: "2026-05-09T01:59:59Z",
      finished_at: "2026-05-09T02:00:00Z",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await forceFailIngestion(
      "ing_xyz",
      {
        idempotency_key: payload.idempotency_key,
        confirmation: "FAIL ing_xyz",
        params: { reason: "stuck" },
      },
      fetcher,
    );
    expect(result).toEqual(payload);
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/ingestions/ing_xyz/force-fail`);
  });
});

describe("blockUserSessions", () => {
  it("returns affected_count and blocked_session_ids on success", async () => {
    const payload: LifecycleResponse<{
      user_id: string;
      affected_count: number;
      blocked_session_ids: string[];
      skipped_count: number;
      noop: boolean;
    }> = {
      idempotency_key: "33333333-3333-4333-8333-333333333333",
      status: "succeeded",
      result: {
        user_id: "user_42",
        affected_count: 2,
        blocked_session_ids: ["sess_a", "sess_b"],
        skipped_count: 0,
        noop: false,
      },
      audit_request_id: "req_003",
      started_at: "2026-05-09T03:00:00Z",
      finished_at: "2026-05-09T03:00:01Z",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await blockUserSessions(
      "user_42",
      {
        idempotency_key: payload.idempotency_key,
        confirmation: "BLOCK USER user_42",
        params: { reason: "abuse" },
      },
      fetcher,
    );
    expect(result.result?.affected_count).toBe(2);
    expect(result.result?.blocked_session_ids).toEqual(["sess_a", "sess_b"]);
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/users/user_42/block-sessions`);
  });

  it("honors a custom baseUrl when provided", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        idempotency_key: "k",
        status: "succeeded",
        result: {
          user_id: "u",
          affected_count: 0,
          blocked_session_ids: [],
          skipped_count: 0,
          noop: true,
        },
        audit_request_id: "r",
        started_at: "2026-05-09T03:00:00Z",
        finished_at: "2026-05-09T03:00:00Z",
      }),
    );
    await blockUserSessions(
      "u",
      { idempotency_key: "k", confirmation: "BLOCK USER u", params: { reason: "x" } },
      fetcher,
      "/custom/admin",
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe("/custom/admin/users/u/block-sessions");
  });

  it("propagates ApiError instances unchanged", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(503));
    try {
      await blockUserSessions(
        "u",
        { idempotency_key: "k", confirmation: "BLOCK USER u", params: { reason: "x" } },
        fetcher,
      );
      expect.fail("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(503);
    }
  });
});

// ---------------------------------------------------------------------------
// Tier 3.2 Phase 2: 5 more lifecycle helpers, mirrored happy-path + URL encode.
// ---------------------------------------------------------------------------

describe("blockTenant", () => {
  it("posts to the encoded tenants path and returns the parsed envelope", async () => {
    const payload: LifecycleResponse<{
      tenant_id: string;
      blocked_at: string;
      blocked_reason: string;
      previously_blocked: boolean;
    }> = {
      idempotency_key: "k1",
      status: "succeeded",
      result: {
        tenant_id: "tenant_42",
        blocked_at: "2026-05-09T03:00:00Z",
        blocked_reason: "abuse",
        previously_blocked: false,
      },
      audit_request_id: "r1",
      started_at: "2026-05-09T03:00:00Z",
      finished_at: "2026-05-09T03:00:00Z",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await blockTenant(
      "tenant_42",
      {
        idempotency_key: "k1",
        confirmation: "BLOCK TENANT tenant_42",
        params: { reason: "abuse" },
      },
      fetcher,
    );
    expect(result).toEqual(payload);
    const [calledUrl, init] = fetcher.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/tenants/tenant_42/block`);
    const body = JSON.parse(init.body as string);
    expect(body.confirmation).toBe("BLOCK TENANT tenant_42");
  });

  it("URL-encodes path-traversal-shaped tenant ids", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        idempotency_key: "k",
        status: "succeeded",
        result: {
          tenant_id: "x",
          blocked_at: "2026-05-09T03:00:00Z",
          blocked_reason: "x",
          previously_blocked: false,
        },
        audit_request_id: "r",
        started_at: "2026-05-09T03:00:00Z",
      }),
    );
    await blockTenant(
      "../../etc",
      { idempotency_key: "k", confirmation: "BLOCK TENANT ../../etc", params: { reason: "x" } },
      fetcher,
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/tenants/..%2F..%2Fetc/block`);
  });
});

describe("revokeApiKey", () => {
  it("posts to the encoded api-keys path and returns the parsed envelope", async () => {
    const payload: LifecycleResponse<{
      api_key_id: string;
      revoked_at: string;
      revoked_reason: string;
      was_active: boolean;
    }> = {
      idempotency_key: "k2",
      status: "succeeded",
      result: {
        api_key_id: "key_active",
        revoked_at: "2026-05-09T03:00:00Z",
        revoked_reason: "rotated",
        was_active: true,
      },
      audit_request_id: "r2",
      started_at: "2026-05-09T03:00:00Z",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await revokeApiKey(
      "key_active",
      {
        idempotency_key: "k2",
        confirmation: "REVOKE KEY key_active",
        params: { reason: "rotated" },
      },
      fetcher,
    );
    expect(result.result?.was_active).toBe(true);
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/api-keys/key_active/revoke`);
  });

  it("URL-encodes path-traversal-shaped api key ids", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        idempotency_key: "k",
        status: "succeeded",
        result: {
          api_key_id: "x",
          revoked_at: "t",
          revoked_reason: "x",
          was_active: true,
        },
        audit_request_id: "r",
        started_at: "t",
      }),
    );
    await revokeApiKey(
      "key/../bad",
      { idempotency_key: "k", confirmation: "REVOKE KEY key/../bad", params: { reason: "x" } },
      fetcher,
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/api-keys/key%2F..%2Fbad/revoke`);
  });
});

describe("killStreamingSession", () => {
  it("posts to the encoded streaming-sessions path and returns the envelope", async () => {
    const payload: LifecycleResponse<{
      session_id: string;
      killed_at: string;
      killed_reason: string;
      eventbridge_event_id: string;
      was_active: boolean;
    }> = {
      idempotency_key: "k3",
      status: "succeeded",
      result: {
        session_id: "sess_x",
        killed_at: "t",
        killed_reason: "x",
        eventbridge_event_id: "evt-1",
        was_active: true,
      },
      audit_request_id: "r3",
      started_at: "t",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await killStreamingSession(
      "sess_x",
      { idempotency_key: "k3", confirmation: "KILL STREAM sess_x", params: { reason: "x" } },
      fetcher,
    );
    expect(result.result?.eventbridge_event_id).toBe("evt-1");
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/streaming-sessions/sess_x/kill`);
  });

  it("URL-encodes path-traversal-shaped session ids", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        idempotency_key: "k",
        status: "succeeded",
        result: {
          session_id: "x",
          killed_at: "t",
          killed_reason: "x",
          eventbridge_event_id: "e",
          was_active: false,
        },
        audit_request_id: "r",
        started_at: "t",
      }),
    );
    await killStreamingSession(
      "sess/../bad",
      { idempotency_key: "k", confirmation: "KILL STREAM sess/../bad", params: { reason: "x" } },
      fetcher,
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/streaming-sessions/sess%2F..%2Fbad/kill`);
  });
});

describe("killBatchJob", () => {
  it("posts to the encoded batch-jobs path and returns the envelope", async () => {
    const payload: LifecycleResponse<{
      job_id: string;
      killed_at: string;
      killed_reason: string;
      batch_terminate_request_id: string;
    }> = {
      idempotency_key: "k4",
      status: "succeeded",
      result: {
        job_id: "job_42",
        killed_at: "t",
        killed_reason: "drill",
        batch_terminate_request_id: "req-x",
      },
      audit_request_id: "r4",
      started_at: "t",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await killBatchJob(
      "job_42",
      { idempotency_key: "k4", confirmation: "KILL JOB job_42", params: { reason: "drill" } },
      fetcher,
    );
    expect(result.result?.batch_terminate_request_id).toBe("req-x");
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/batch-jobs/job_42/kill`);
  });

  it("propagates ApiError on non-2xx", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(500));
    await expect(
      killBatchJob(
        "job_42",
        { idempotency_key: "k", confirmation: "KILL JOB job_42", params: { reason: "x" } },
        fetcher,
      ),
    ).rejects.toBeInstanceOf(ApiError);
  });
});

describe("forceBillingRecompute", () => {
  it("posts to the encoded force-billing-recompute path and returns the envelope", async () => {
    const payload: LifecycleResponse<{
      tenant_id: string;
      queued_at: string;
      queued_reason: string;
      eventbridge_event_id: string;
    }> = {
      idempotency_key: "k5",
      status: "succeeded",
      result: {
        tenant_id: "tenant_42",
        queued_at: "t",
        queued_reason: "month-end",
        eventbridge_event_id: "evt-9",
      },
      audit_request_id: "r5",
      started_at: "t",
    };
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(payload));
    const result = await forceBillingRecompute(
      "tenant_42",
      {
        idempotency_key: "k5",
        confirmation: "RECOMPUTE BILLING tenant_42",
        params: { reason: "month-end" },
      },
      fetcher,
    );
    expect(result.result?.eventbridge_event_id).toBe("evt-9");
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/tenants/tenant_42/force-billing-recompute`);
  });

  it("URL-encodes path-traversal-shaped tenant ids", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        idempotency_key: "k",
        status: "succeeded",
        result: { tenant_id: "x", queued_at: "t", queued_reason: "x", eventbridge_event_id: "e" },
        audit_request_id: "r",
        started_at: "t",
      }),
    );
    await forceBillingRecompute(
      "tenant/../etc",
      {
        idempotency_key: "k",
        confirmation: "RECOMPUTE BILLING tenant/../etc",
        params: { reason: "x" },
      },
      fetcher,
    );
    const [calledUrl] = fetcher.mock.calls[0] as [string];
    expect(calledUrl).toBe(`${ADMIN_API_BASE}/tenants/tenant%2F..%2Fetc/force-billing-recompute`);
  });
});

describe("generateIdempotencyKey", () => {
  it("produces a UUIDv4-shaped string", () => {
    const key = generateIdempotencyKey();
    expect(isUuidV4(key)).toBe(true);
  });

  it("produces a fresh value on each call", () => {
    const a = generateIdempotencyKey();
    const b = generateIdempotencyKey();
    expect(a).not.toBe(b);
  });
});
