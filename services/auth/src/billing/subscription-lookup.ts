/**
 * Plan-claim lookup for the auth service.
 *
 * At sign-in / sign-up time, the auth service queries the
 * `panakoes-dev-subscriptions` DynamoDB table (provisioned by the billing
 * slice; see services/billing/README.md for the writer contract) to discover
 * the highest-tier ACTIVE subscription for the freshly-authenticated user
 * and bakes that tier into the JWT's `plan` claim. Downstream services then
 * gate features via `middleware-lib`'s `require_plan(...)` without needing
 * their own DDB round-trip on every request.
 *
 * Schema assumed (forward-referenced from PR #247, the Stripe webhooks +
 * subscriptions table slice):
 *
 *   pk  = tenant_id   (partition key)
 *   sk  = subscription_id   (sort key; lets multiple historical subs sit
 *                            under one tenant_id without overwriting)
 *
 *   plan                : "free" | "pro" | "team"
 *   status              : "active" | "canceled" | "past_due" | ...
 *   current_period_end  : ISO 8601 timestamp (informational here)
 *
 * Multi-tenant follow-up (ADR-XX, not in this PR):
 *   In v0.1 dev, each authenticated user IS a tenant, so we lookup with
 *   tenant_id = user.id. When real multi-tenant lands (a user belongs to
 *   one or more orgs and inherits the org's plan), the lookup must resolve
 *   user.id -> tenant_id(s) via a membership table first, then pick the
 *   highest-tier active subscription across all owning tenants. The
 *   signature of `getActivePlan` is kept narrow (one user id in, one plan
 *   out) precisely so the v0.2 swap is a single-function change.
 *
 * Caching:
 *   In-memory 60-second TTL keyed by tenant_id. Stripe webhooks land on
 *   services/billing/ and we deliberately do NOT plumb a cross-service
 *   invalidation channel back to auth: the 60s TTL bounds staleness to one
 *   minute, which is acceptable for a plan-tier read in a v0.1 dev system
 *   where the customer just upgraded and re-signs-in to pick up the new
 *   tier anyway. The cache is per-process (auth runs as a single ECS task
 *   in dev). A future ElastiCache layer with webhook-driven invalidation
 *   is the v0.3 follow-up; same `getActivePlan` signature, different
 *   internals.
 *
 * Fail-closed posture:
 *   ANY DDB error (network failure, ProvisionedThroughputExceeded,
 *   ResourceNotFoundException because the table has not yet been
 *   provisioned, AccessDeniedException because the task role grant is
 *   missing) returns "free". We NEVER elevate the plan claim through an
 *   error path; the worst customer-visible outcome of a flaky DDB is a
 *   "your Pro features look gated; sign out and back in" support ticket,
 *   never a free user seeing Pro features. This is the same posture as
 *   middleware-lib's `require_plan` evaluating a missing claim as "free".
 */

import type { DynamoDBClient, QueryCommandInput } from "@aws-sdk/client-dynamodb";
import { QueryCommand } from "@aws-sdk/client-dynamodb";

import type { Logger } from "../logger.ts";

/**
 * The set of plan tiers we surface in the JWT claim. Ordered from lowest
 * to highest precedence so the array index doubles as a comparator: index
 * 0 ("free") < index 1 ("pro") < index 2 ("team").
 */
export const PLAN_TIERS = ["free", "pro", "team"] as const;
export type Plan = (typeof PLAN_TIERS)[number];

/**
 * 60-second TTL bounds plan-claim staleness to one minute. See module
 * docstring for the rationale.
 */
export const PLAN_CACHE_TTL_MS = 60_000;

/**
 * Subscription statuses we count as "the user is currently entitled to
 * their plan tier". Anything outside this set degrades to "free". Stripe
 * surfaces "trialing" as a paid-tier-equivalent state; we count it as
 * active so trial users immediately see their tier's features.
 */
const ACTIVE_STATUSES: ReadonlySet<string> = new Set(["active", "trialing"]);

/**
 * Pick the highest-tier plan from a set of plan strings. Unknown plan
 * strings are skipped (defensive against a future Stripe price label that
 * predates the auth service's deploy of this code). Returns "free" if the
 * input set is empty or contains only unknown plans.
 */
function pickHighestPlan(plans: readonly string[]): Plan {
  let best: Plan = "free";
  let bestRank = 0;
  for (const candidate of plans) {
    const rank = PLAN_TIERS.indexOf(candidate as Plan);
    if (rank > bestRank) {
      bestRank = rank;
      best = PLAN_TIERS[rank] as Plan;
    }
  }
  return best;
}

export interface PlanLookupDeps {
  /**
   * DynamoDB client. Injected (not constructed inline) so tests can swap
   * in a stub and so production wiring can share a single client across
   * the process for connection-pool reuse.
   */
  ddb: Pick<DynamoDBClient, "send">;

  /** The provisioned subscriptions table name. */
  tableName: string;

  logger: Logger;

  /**
   * Clock seam, defaults to `Date.now`. Lets tests advance "time" without
   * vi.useFakeTimers polluting other async paths.
   */
  now?: () => number;
}

export interface PlanLookup {
  /**
   * Resolve the active plan for `userId`. Returns "free" on:
   *   - no subscriptions row,
   *   - all rows in non-active statuses,
   *   - any DDB error.
   */
  getActivePlan: (userId: string) => Promise<Plan>;

  /**
   * Clear the in-memory cache. Exposed for tests; production code does
   * not call this directly.
   */
  clearCache: () => void;
}

interface CacheEntry {
  plan: Plan;
  expiresAt: number;
}

/**
 * Construct a plan-lookup with its own in-memory cache. Returning a
 * factory (rather than a free function) keeps the cache scoped to the
 * caller's lifetime and makes the cache trivially resettable in tests.
 */
export function createPlanLookup(deps: PlanLookupDeps): PlanLookup {
  const { ddb, tableName, logger } = deps;
  const now = deps.now ?? (() => Date.now());
  const cache = new Map<string, CacheEntry>();

  async function queryActivePlan(userId: string): Promise<Plan> {
    // v0.1 assumption: tenant_id === user.id. See module docstring.
    const input: QueryCommandInput = {
      TableName: tableName,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: {
        ":pk": { S: userId },
      },
    };

    const result = await ddb.send(new QueryCommand(input));
    const items = result.Items ?? [];

    const activePlans: string[] = [];
    for (const item of items) {
      const status = item.status?.S;
      const plan = item.plan?.S;
      if (status && plan && ACTIVE_STATUSES.has(status)) {
        activePlans.push(plan);
      }
    }

    return pickHighestPlan(activePlans);
  }

  async function getActivePlan(userId: string): Promise<Plan> {
    const cached = cache.get(userId);
    if (cached && cached.expiresAt > now()) {
      return cached.plan;
    }

    let plan: Plan;
    try {
      plan = await queryActivePlan(userId);
    } catch (err) {
      // Fail-closed: any DDB error returns "free". We log so operators
      // see flaky DDB / missing-table / missing-IAM-grant, but we NEVER
      // elevate the plan claim through an error path.
      const message = err instanceof Error ? err.message : "unknown";
      logger.warn(
        { err: message, userId, tableName },
        "subscription lookup failed; defaulting plan to free",
      );
      plan = "free";
    }

    cache.set(userId, { plan, expiresAt: now() + PLAN_CACHE_TTL_MS });
    return plan;
  }

  function clearCache(): void {
    cache.clear();
  }

  return { getActivePlan, clearCache };
}
