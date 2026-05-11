/**
 * Integration tests: the plan claim baked into a freshly-minted JWT
 * reflects what the subscription lookup returned.
 *
 * The four paths from the brief, wired end-to-end through sign-up /
 * sign-in:
 *   1. pro user                 -> JWT carries plan: "pro"
 *   2. team user                -> JWT carries plan: "team"
 *   3. free user (no row)       -> JWT carries plan: "free"
 *   4. free user (canceled)     -> JWT carries plan: "free"
 *
 * The lookup is stubbed via `buildTestApp(..., stubPlanLookup(plan))`.
 * The unit test file (`tests/unit/subscription-lookup.test.ts`) covers
 * the DDB query semantics that actually translate a row's state into a
 * plan; here we only verify the route correctly threads the lookup's
 * return value through `signJwt` into the verified token.
 */

import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { verifyJwt } from "../../src/auth/jwt.ts";
import type { Plan } from "../../src/billing/subscription-lookup.ts";
import { buildTestApp, jsonRequest, stubPlanLookup, type TestApp, truncateAll } from "../helpers.ts";

function appWithPlan(plan: Plan): TestApp {
  return buildTestApp({}, stubPlanLookup(plan));
}

interface AuthBody {
  token: string;
  user: { id: string; email: string; role: string };
}

describe("plan claim in signed JWT (sign-up path)", () => {
  for (const plan of ["free", "pro", "team"] as const) {
    describe(`when lookup returns '${plan}'`, () => {
      const app = appWithPlan(plan);

      beforeEach(async () => {
        await truncateAll(app.db);
      });
      afterAll(async () => {
        await app.cleanup();
      });

      it(`bakes plan: '${plan}' into the sign-up JWT`, async () => {
        const res = await jsonRequest(app, "/sign-up", {
          body: { email: `${plan}-signup@example.com`, password: "correct horse battery staple" },
        });
        expect(res.status).toBe(201);
        const body = res.body as AuthBody;
        const verified = await verifyJwt(body.token, app.config);
        expect(verified.ok).toBe(true);
        if (verified.ok) {
          expect(verified.claims.plan).toBe(plan);
        }
      });

      it(`bakes plan: '${plan}' into the sign-in JWT`, async () => {
        const email = `${plan}-signin@example.com`;
        const signup = await jsonRequest(app, "/sign-up", {
          body: { email, password: "correct horse battery staple" },
        });
        expect(signup.status).toBe(201);

        const res = await jsonRequest(app, "/sign-in", {
          body: { email, password: "correct horse battery staple" },
        });
        expect(res.status).toBe(200);
        const body = res.body as AuthBody;
        const verified = await verifyJwt(body.token, app.config);
        expect(verified.ok).toBe(true);
        if (verified.ok) {
          expect(verified.claims.plan).toBe(plan);
        }
      });
    });
  }

  describe("canceled-subscription path (lookup correctly returns 'free')", () => {
    // This is the same wire-shape as the no-row case at the routes layer;
    // the unit suite proves that a canceled-status row in DDB resolves
    // to "free" inside the lookup itself. We assert here that the route
    // pipes that "free" through to the token.
    const app = appWithPlan("free");

    beforeEach(async () => {
      await truncateAll(app.db);
    });
    afterAll(async () => {
      await app.cleanup();
    });

    it("bakes plan: 'free' when the user's only subscription is canceled", async () => {
      const res = await jsonRequest(app, "/sign-up", {
        body: { email: "canceled@example.com", password: "correct horse battery staple" },
      });
      expect(res.status).toBe(201);
      const body = res.body as AuthBody;
      const verified = await verifyJwt(body.token, app.config);
      expect(verified.ok).toBe(true);
      if (verified.ok) {
        expect(verified.claims.plan).toBe("free");
      }
    });
  });
});
