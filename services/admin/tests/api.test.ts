import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  HEALTH_ENDPOINT,
  fetchHealth,
  fetchServiceDetail,
  isSnapshotDegraded,
} from "../src/lib/api";
import type { HealthSnapshot, ServiceDetail } from "../src/lib/types";

const okJson = <T>(payload: T): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

const errorResponse = (status: number): Response => new Response("error", { status });

const sampleSnapshot: HealthSnapshot = {
  generated_at: "2026-05-08T12:00:00.000Z",
  services: [
    {
      service: "auth",
      display_name: "Auth",
      status: "healthy",
      last_check: "2026-05-08T11:59:50.000Z",
    },
  ],
};

describe("fetchHealth", () => {
  it("returns the parsed snapshot when the endpoint responds 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleSnapshot));
    const result = await fetchHealth(fetcher);
    expect(result).toEqual(sampleSnapshot);
    expect(fetcher).toHaveBeenCalledWith(HEALTH_ENDPOINT, {
      headers: { Accept: "application/json" },
    });
  });

  it("uses an injected endpoint when provided", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleSnapshot));
    await fetchHealth(fetcher, "/custom/health.json");
    expect(fetcher).toHaveBeenCalledWith("/custom/health.json", {
      headers: { Accept: "application/json" },
    });
  });

  it("throws ApiError on non-2xx with status preserved", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(503));
    await expect(fetchHealth(fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      url: HEALTH_ENDPOINT,
    });
  });

  it("throws ApiError that is also an Error", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(404));
    try {
      await fetchHealth(fetcher);
      expect.fail("expected throw");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect(err).toBeInstanceOf(Error);
    }
  });
});

describe("fetchServiceDetail", () => {
  const sampleDetail: ServiceDetail = {
    service: "auth",
    display_name: "Auth",
    health: {
      service: "auth",
      display_name: "Auth",
      status: "healthy",
      last_check: "2026-05-08T11:59:50.000Z",
    },
    recent_logs: [],
    recent_errors: [],
    metrics: { cpu_percent: 1.0, memory_mb: 64, memory_limit_mb: 512 },
  };

  it("returns parsed detail on 200", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleDetail));
    const result = await fetchServiceDetail("auth", fetcher);
    expect(result).toEqual(sampleDetail);
    expect(fetcher).toHaveBeenCalledWith("/dashboard/auth.json", {
      headers: { Accept: "application/json" },
    });
  });

  it("encodes the service id into the URL path", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(sampleDetail));
    await fetchServiceDetail("transcriber-stream", fetcher);
    expect(fetcher).toHaveBeenCalledWith("/dashboard/transcriber-stream.json", {
      headers: { Accept: "application/json" },
    });
  });

  it("throws ApiError with the right URL on 404", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(404));
    await expect(fetchServiceDetail("missing", fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 404,
      url: "/dashboard/missing.json",
    });
  });
});

describe("isSnapshotDegraded", () => {
  it("returns false when all services are healthy", () => {
    expect(isSnapshotDegraded(sampleSnapshot)).toBe(false);
  });

  it("returns true when at least one service is unhealthy", () => {
    const degraded: HealthSnapshot = {
      ...sampleSnapshot,
      services: [
        ...sampleSnapshot.services,
        {
          service: "x",
          display_name: "X",
          status: "unhealthy",
          last_check: "2026-05-08T11:59:50.000Z",
        },
      ],
    };
    expect(isSnapshotDegraded(degraded)).toBe(true);
  });

  it("returns false when only unknown services are present", () => {
    const unknown: HealthSnapshot = {
      generated_at: sampleSnapshot.generated_at,
      services: [
        {
          service: "x",
          display_name: "X",
          status: "unknown",
          last_check: "2026-05-08T11:59:50.000Z",
        },
      ],
    };
    expect(isSnapshotDegraded(unknown)).toBe(false);
  });
});

describe("ApiError", () => {
  it("captures the message, status, url, and inherits from Error", () => {
    const err = new ApiError("boom", 500, "/x");
    expect(err.message).toBe("boom");
    expect(err.status).toBe(500);
    expect(err.url).toBe("/x");
    expect(err.name).toBe("ApiError");
    expect(err).toBeInstanceOf(Error);
  });
});

import { AUDIT_LOG_ENDPOINT, fetchAuditLog } from "../src/lib/api";
import type { AuditLogPage } from "../src/lib/types";

const samplePage: AuditLogPage = {
  entries: [
    {
      request_id: "r-1",
      timestamp: "2026-05-09T00:00:00Z",
      source_service: "admin-api",
      actor_id: "user_admin",
      action: "tier3.terminate-session.intent",
      tier3_action: "terminate-session",
      payload: { outcome: "pending" },
    },
  ],
  next_cursor: null,
  generated_at: "2026-05-09T00:00:01Z",
};

describe("fetchAuditLog", () => {
  it("requests the default endpoint with no query when no filters supplied", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(samplePage));
    const result = await fetchAuditLog({}, fetcher);
    expect(result).toEqual(samplePage);
    expect(fetcher).toHaveBeenCalledWith(AUDIT_LOG_ENDPOINT, {
      headers: { Accept: "application/json" },
    });
  });

  it("encodes tier3_action, cursor, and limit into the query string", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(samplePage));
    await fetchAuditLog({ tier3_action: "terminate-session", cursor: "abc==", limit: 10 }, fetcher);
    const calledUrl = fetcher.mock.calls[0][0];
    expect(calledUrl).toContain(`${AUDIT_LOG_ENDPOINT}?`);
    expect(calledUrl).toContain("tier3_action=terminate-session");
    expect(calledUrl).toContain("limit=10");
    // URLSearchParams percent-encodes "==" as "%3D%3D".
    expect(calledUrl).toContain("cursor=abc");
  });

  it("omits empty filter fields", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(samplePage));
    await fetchAuditLog({ tier3_action: "", cursor: "" }, fetcher);
    expect(fetcher).toHaveBeenCalledWith(AUDIT_LOG_ENDPOINT, {
      headers: { Accept: "application/json" },
    });
  });

  it("throws ApiError on non-2xx with the audit-log URL preserved", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errorResponse(403));
    await expect(fetchAuditLog({}, fetcher)).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
      url: AUDIT_LOG_ENDPOINT,
    });
  });
});
