/**
 * Unit tests for the plan-claim lookup.
 *
 * The four core paths the brief required:
 *   1. pro user                 -> returns "pro"
 *   2. team user                -> returns "team"
 *   3. free user (no row)       -> returns "free"
 *   4. free user (canceled)     -> returns "free"
 *
 * Plus coverage for:
 *   - tier-precedence (team > pro > free) when multiple active subs exist
 *   - the 60s in-memory cache TTL
 *   - fail-closed behaviour on any DDB error (logged + "free", never elevated)
 *   - unknown plan strings degrade to "free"
 *   - unknown statuses (e.g. "incomplete", "past_due") degrade to "free"
 *   - clearCache() boots the entry out for the next call
 */

import { QueryCommand } from "@aws-sdk/client-dynamodb";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createPlanLookup,
  PLAN_CACHE_TTL_MS,
  type PlanLookupDeps,
} from "../../src/billing/subscription-lookup.ts";

interface FakeRow {
  plan?: string;
  status?: string;
}

function ddbItem(row: FakeRow): Record<string, { S: string }> {
  const out: Record<string, { S: string }> = {};
  if (row.plan !== undefined) {
    out.plan = { S: row.plan };
  }
  if (row.status !== undefined) {
    out.status = { S: row.status };
  }
  return out;
}

function fakeDdb(rows: FakeRow[]): {
  ddb: Pick<import("@aws-sdk/client-dynamodb").DynamoDBClient, "send">;
  sendCount: () => number;
} {
  let count = 0;
  return {
    sendCount: () => count,
    ddb: {
      send: vi.fn(async (cmd: unknown) => {
        count += 1;
        // Type guard: assert it's a QueryCommand-shaped call.
        expect(cmd).toBeInstanceOf(QueryCommand);
        return { Items: rows.map(ddbItem) };
      }) as unknown as Pick<
        import("@aws-sdk/client-dynamodb").DynamoDBClient,
        "send"
      >["send"],
    },
  };
}

function fakeDdbError(err: Error): {
  ddb: Pick<import("@aws-sdk/client-dynamodb").DynamoDBClient, "send">;
} {
  return {
    ddb: {
      send: vi.fn(async () => {
        throw err;
      }) as unknown as Pick<
        import("@aws-sdk/client-dynamodb").DynamoDBClient,
        "send"
      >["send"],
    },
  };
}

interface TestLogger {
  warn: ReturnType<typeof vi.fn>;
  info: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
  debug: ReturnType<typeof vi.fn>;
}

function silentLogger(): TestLogger {
  return {
    warn: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  };
}

const TABLE = "panakoes-test-subscriptions";

function build(
  partial: Partial<PlanLookupDeps> & Pick<PlanLookupDeps, "ddb">,
): { lookup: ReturnType<typeof createPlanLookup>; logger: TestLogger } {
  const logger = silentLogger();
  return {
    logger,
    lookup: createPlanLookup({
      ddb: partial.ddb,
      tableName: partial.tableName ?? TABLE,
      logger: (partial.logger ?? logger) as PlanLookupDeps["logger"],
      now: partial.now,
    }),
  };
}

describe("subscription-lookup: getActivePlan", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 'pro' for a user with an active pro subscription", async () => {
    const { ddb } = fakeDdb([{ plan: "pro", status: "active" }]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-pro")).resolves.toBe("pro");
  });

  it("returns 'team' for a user with an active team subscription", async () => {
    const { ddb } = fakeDdb([{ plan: "team", status: "active" }]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-team")).resolves.toBe("team");
  });

  it("returns 'free' for a user with no subscription rows", async () => {
    const { ddb } = fakeDdb([]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-free")).resolves.toBe("free");
  });

  it("returns 'free' for a user whose only subscription is canceled", async () => {
    const { ddb } = fakeDdb([{ plan: "pro", status: "canceled" }]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-canceled")).resolves.toBe("free");
  });

  it("treats Stripe's 'trialing' status as active (entitled to tier)", async () => {
    const { ddb } = fakeDdb([{ plan: "pro", status: "trialing" }]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-trialing")).resolves.toBe("pro");
  });

  it("picks the highest tier when multiple active subscriptions exist", async () => {
    const { ddb } = fakeDdb([
      { plan: "pro", status: "active" },
      { plan: "team", status: "active" },
      { plan: "free", status: "active" },
    ]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-multi")).resolves.toBe("team");
  });

  it("skips rows missing plan or status (defensive against partial writes)", async () => {
    const { ddb } = fakeDdb([
      { plan: "pro" }, // no status
      { status: "active" }, // no plan
      { plan: "team", status: "active" },
    ]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-partial")).resolves.toBe("team");
  });

  it("ignores unknown plan strings (forward-compatibility with a future tier)", async () => {
    const { ddb } = fakeDdb([
      { plan: "enterprise", status: "active" },
      { plan: "pro", status: "active" },
    ]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-unknown-plan")).resolves.toBe("pro");
  });

  it("treats unknown statuses as not-entitled (past_due, incomplete, etc.)", async () => {
    const { ddb } = fakeDdb([
      { plan: "pro", status: "past_due" },
      { plan: "team", status: "incomplete" },
    ]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-not-entitled")).resolves.toBe("free");
  });

  it("returns 'free' (and logs) when DDB throws (fail-closed)", async () => {
    const { ddb } = fakeDdbError(new Error("throughput exceeded"));
    const { lookup, logger } = build({ ddb });
    await expect(lookup.getActivePlan("user-flaky-ddb")).resolves.toBe("free");
    expect(logger.warn).toHaveBeenCalledOnce();
    const warnArgs = logger.warn.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(warnArgs).toMatchObject({
      err: "throughput exceeded",
      userId: "user-flaky-ddb",
      tableName: TABLE,
    });
  });

  it("returns 'free' when DDB throws a non-Error value (defensive)", async () => {
    // Force-throw a non-Error to cover the `err instanceof Error` else
    // branch. We cast through unknown so the lint/type-check rules do
    // not block the deliberate misuse.
    const ddb = {
      send: vi.fn(async () => {
        throw "string error" as unknown as Error;
      }),
    } as unknown as Pick<import("@aws-sdk/client-dynamodb").DynamoDBClient, "send">;
    const { lookup, logger } = build({ ddb });
    await expect(lookup.getActivePlan("user-weird-throw")).resolves.toBe("free");
    expect(logger.warn).toHaveBeenCalledOnce();
    expect((logger.warn.mock.calls[0]?.[0] as Record<string, unknown>).err).toBe("unknown");
  });

  it("handles a DDB response with no Items field (returns 'free')", async () => {
    const ddb = {
      send: vi.fn(async () => ({})),
    } as unknown as Pick<import("@aws-sdk/client-dynamodb").DynamoDBClient, "send">;
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("user-empty-response")).resolves.toBe("free");
  });
});

describe("subscription-lookup: cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("caches a successful lookup for PLAN_CACHE_TTL_MS milliseconds", async () => {
    const { ddb, sendCount } = fakeDdb([{ plan: "pro", status: "active" }]);
    let clock = 1_000_000;
    const { lookup } = build({ ddb, now: () => clock });

    expect(await lookup.getActivePlan("u")).toBe("pro");
    expect(sendCount()).toBe(1);

    // 30s later: still cached.
    clock += 30_000;
    expect(await lookup.getActivePlan("u")).toBe("pro");
    expect(sendCount()).toBe(1);

    // Past the TTL: re-queries.
    clock += PLAN_CACHE_TTL_MS;
    expect(await lookup.getActivePlan("u")).toBe("pro");
    expect(sendCount()).toBe(2);
  });

  it("caches the fail-closed 'free' result so a flapping DDB does not retry-storm", async () => {
    const { ddb } = fakeDdbError(new Error("network blip"));
    let clock = 1_000_000;
    const { lookup, logger } = build({ ddb, now: () => clock });

    expect(await lookup.getActivePlan("u")).toBe("free");
    expect(await lookup.getActivePlan("u")).toBe("free");
    // One DDB call total; the second served from cache.
    expect(logger.warn).toHaveBeenCalledOnce();
  });

  it("clearCache() forces the next call to re-query DDB", async () => {
    const { ddb, sendCount } = fakeDdb([{ plan: "team", status: "active" }]);
    const { lookup } = build({ ddb });

    await lookup.getActivePlan("u");
    await lookup.getActivePlan("u"); // cached
    expect(sendCount()).toBe(1);

    lookup.clearCache();
    await lookup.getActivePlan("u"); // re-queries
    expect(sendCount()).toBe(2);
  });

  it("uses Date.now by default when no `now` is supplied", async () => {
    // Constructing without `now` exercises the default-clock branch.
    const { ddb } = fakeDdb([{ plan: "pro", status: "active" }]);
    const { lookup } = build({ ddb });
    await expect(lookup.getActivePlan("u")).resolves.toBe("pro");
    await expect(lookup.getActivePlan("u")).resolves.toBe("pro"); // cached
  });
});

describe("subscription-lookup: query shape", () => {
  it("queries with pk = tenant_id (the v0.1 user.id-as-tenant assumption)", async () => {
    const send = vi.fn(async () => ({ Items: [] }));
    const ddb = { send } as unknown as Pick<
      import("@aws-sdk/client-dynamodb").DynamoDBClient,
      "send"
    >;
    const { lookup } = build({ ddb });
    await lookup.getActivePlan("user-42");

    expect(send).toHaveBeenCalledOnce();
    const cmd = send.mock.calls[0]?.[0] as QueryCommand;
    expect(cmd).toBeInstanceOf(QueryCommand);
    expect(cmd.input).toMatchObject({
      TableName: TABLE,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": { S: "user-42" } },
    });
  });
});
