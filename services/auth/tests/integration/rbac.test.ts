import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { buildTestApp, jsonRequest, truncateAll } from "../helpers.ts";

const app = buildTestApp();

beforeEach(async () => {
  await truncateAll(app.db);
});

afterAll(async () => {
  await app.cleanup();
});

interface RoleRow {
  email: string;
  role: string;
}

describe("user table RBAC migration", () => {
  it("defaults new users to role=user", async () => {
    const res = await jsonRequest(app, "/auth/sign-up", {
      body: { email: "rbac1@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(201);

    const rows = (await app.db.execute(
      `SELECT email, role FROM "user" WHERE email = 'rbac1@example.com'`,
    )) as unknown as RoleRow[];
    expect(rows.length).toBe(1);
    expect(rows[0]?.role).toBe("user");
  });

  it("accepts admin assignment via SQL update", async () => {
    const res = await jsonRequest(app, "/auth/sign-up", {
      body: { email: "rbac2@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(201);
    await app.db.execute(`UPDATE "user" SET role = 'admin' WHERE email = 'rbac2@example.com'`);
    const rows = (await app.db.execute(
      `SELECT email, role FROM "user" WHERE email = 'rbac2@example.com'`,
    )) as unknown as RoleRow[];
    expect(rows[0]?.role).toBe("admin");
  });

  it("rejects unknown roles via the CHECK constraint", async () => {
    const res = await jsonRequest(app, "/auth/sign-up", {
      body: { email: "rbac3@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(201);
    await expect(
      app.db.execute(`UPDATE "user" SET role = 'superuser' WHERE email = 'rbac3@example.com'`),
    ).rejects.toThrow();
  });
});
