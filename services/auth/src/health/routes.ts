/**
 * `/health` liveness endpoint. Returns the canonical `{status, service}`
 * shape every Panakoes service exposes for ALB / k8s probes.
 */
import { Hono } from "hono";

export function createHealthRoutes(): Hono {
  const app = new Hono();
  app.get("/health", (c) => c.json({ status: "ok", service: "auth" }));
  return app;
}
