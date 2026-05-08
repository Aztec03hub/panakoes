import { afterAll, describe, expect, it } from "vitest";

import { buildTestApp, jsonRequest } from "../helpers.ts";

const app = buildTestApp();

afterAll(async () => {
  await app.cleanup();
});

describe("GET /.well-known/jwks.json", () => {
  it("returns 200 with an empty key set in slice 1 (HS256)", async () => {
    const res = await jsonRequest(app, "/.well-known/jwks.json", { method: "GET" });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ keys: [] });
  });

  it("sets a Cache-Control header so verifiers can cache the response", async () => {
    const res = await jsonRequest(app, "/.well-known/jwks.json", { method: "GET" });
    const cache = res.headers.get("cache-control");
    expect(cache).toBeTruthy();
    expect(cache).toContain("max-age=");
  });
});
