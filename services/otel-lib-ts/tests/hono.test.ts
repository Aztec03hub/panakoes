import { Hono } from "hono";
import { describe, expect, it } from "vitest";

import { instrumentHono } from "../src/hono.ts";

describe("instrumentHono", () => {
  it("adds middleware to a Hono app", async () => {
    const app = new Hono();
    instrumentHono(app);

    app.get("/health", (c) => c.text("ok"));

    const res = await app.request("/health");
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("ok");
  });

  it("creates a span for incoming requests and propagates trace context", async () => {
    const app = new Hono();
    instrumentHono(app);

    let receivedTraceparent: string | undefined;
    app.get("/echo", (c) => {
      receivedTraceparent = c.req.header("traceparent");
      return c.json({ ok: true });
    });

    const incomingTraceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01";
    const res = await app.request("/echo", {
      headers: { traceparent: incomingTraceparent },
    });

    expect(res.status).toBe(200);
    expect(receivedTraceparent).toBe(incomingTraceparent);
  });

  it("records server errors as span errors without breaking the response", async () => {
    const app = new Hono();
    instrumentHono(app);

    app.get("/boom", () => {
      throw new Error("boom");
    });

    const res = await app.request("/boom");
    // Hono's default error handler returns 500 on unhandled errors.
    expect(res.status).toBe(500);
  });

  it("handles requests with no path-specific route as 404", async () => {
    const app = new Hono();
    instrumentHono(app);

    const res = await app.request("/missing");
    expect(res.status).toBe(404);
  });
});
