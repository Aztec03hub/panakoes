import { describe, expect, it, vi } from "vitest";
import {
  ADMIN_API_BASE,
  ApiError,
  blockUserSessions,
  forceFailIngestion,
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
