import { afterAll, describe, expect, it } from "vitest";

import { buildTestApp, jsonRequest } from "../helpers.ts";

const app = buildTestApp();

afterAll(async () => {
  await app.cleanup();
});

describe("GET /health", () => {
  it("returns 200 with the expected payload", async () => {
    const res = await jsonRequest(app, "/health", { method: "GET" });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok", service: "auth" });
  });
});
