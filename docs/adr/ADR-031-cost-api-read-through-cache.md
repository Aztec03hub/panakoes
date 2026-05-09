# ADR-031: cost-api read-through cache with surfaced cache_hit signal

## Status

Accepted. Lived since 2026-05-09 (Phase 1.1-1.3 of the Tier 2 dashboard).

## Context

Tier 2 of the admin dashboard surfaces AWS spend in three views: by service (Phase 1), by tenant rollup (Phase 2), forecast (Phase 2), and anomalies (Phase 2). Every view ultimately reads from AWS Cost Explorer. CE has two properties that shape this design:

1. **Latency.** A typical `GetCostAndUsage` call takes 1-3 seconds. A dashboard page that fires three CE calls on render takes 3-9 seconds, which is too slow for an "operational at-a-glance" view.
2. **Per-query cost.** CE charges $0.01 per paginated API request beyond a free monthly quota of 1000 calls. A small team poking at the dashboard tens of times a day breaches that quota in two weeks.

We need:
- Sub-second renders on repeat views.
- A bound on CE query rate that survives a developer holding F5 on a page.
- Operator visibility when a value is stale, so a human investigating a spike can tell whether they're looking at a snapshot from 30 seconds ago or 30 minutes ago.

## Decision

Every cost-api endpoint reads-through a DynamoDB cache before paying the CE round trip.

The cache lives in a dedicated `panakoes-dev-cost-cache` table provisioned by `infra/dev/admin-state/`. Hash key is `cache_key` (a SHA-256 hash of a deterministic JSON serialization of the structured query parameters); the cache value is the JSON-serialized response envelope. TTL on `expires_at` (24h max) is a safety net against operationally stale data; per-route TTLs are typically much shorter (1 hour for the by-service view).

The orchestrator is `CostCache.cache_or_fetch(key, fetch)`:

```python
async def cache_or_fetch(self, key, fetch, ttl_seconds=DEFAULT_TTL_SECONDS):
    cached = self.get(key)
    if cached is not None:
        return cached
    fresh = await fetch()
    self.put(key, fresh, ttl_seconds=ttl_seconds)
    return fresh
```

Two operational-visibility properties make this discoverable to humans:

1. **The response envelope carries a `cache_hit: bool` field.** The cache flips this to `True` when serving from DynamoDB and leaves it `False` when the underlying CE wrapper returns a fresh result. The dashboard renders the flag verbatim ("Served from cache" / "Fresh from Cost Explorer") above the data table. An operator looking at unexpected numbers can tell at a glance whether they're seeing a stale snapshot.
2. **The envelope carries a server-trusted `queried_at: datetime`.** Set by the route layer at the moment the response is assembled (cache hit or miss). Lets the dashboard render staleness without trusting the client clock.

The cache flips `cache_hit` on read by `model_copy(update={"cache_hit": True})`. The stored envelope is always written with `cache_hit=False` (its value at the moment of original CE fetch), so the cache is also a faithful record of the original response shape.

## Consequences

**Positive.**

- Dashboard pages render in tens of milliseconds for cache hits. The CE round trip happens at most once per (cache_key, TTL window) interval per environment.
- CE per-query fees are bounded. A typical day produces dozens of CE calls (one per first-render of each cache entry), not thousands.
- The `cache_hit` flag is the operational signal that lets a human investigating a spike or anomaly distinguish "the dashboard hasn't refreshed yet" from "the underlying spend genuinely changed."
- Cache invalidation is implicit (TTL expiry) rather than explicit (purge-on-write). Operations that change cost-relevant state (a new tenant onboarding, a cost-allocation tag rename) become visible at the next TTL boundary, no orchestration required.

**Negative.**

- Stale data within the TTL window. A new tenant onboarded 5 minutes ago will not appear in the by-service breakdown for up to an hour (the default TTL). The `cache_hit` operational signal makes this discoverable; the design accepts the trade-off because the alternative (purge-on-write) requires the cost-api to know about every event that could shift cost attribution, which is not a tractable bound.
- Cache rows survive 24h even if their TTL is shorter, because DynamoDB TTL deletion is asynchronous (within ~48h of `expires_at`). The `cache_or_fetch` path bypasses this by checking the cached envelope's freshness against `queried_at` rather than trusting DynamoDB's sweep. Phase 1 does not need that precision; we pay the rule "trust DynamoDB to sweep eventually."
- Cache_key collisions are possible in principle but vanishingly unlikely (SHA-256 of structured JSON). Acceptable.

## Alternatives considered

**No cache, hit CE on every request.** Rejected: 3-9 second page renders, $30+/month in CE fees per developer poking at the dashboard, free-tier quota exhaustion in two weeks.

**In-memory cache (per-process).** Rejected: cost-api will run multi-replica behind ECS; in-memory caches diverge per replica. A repeat view would land on a different replica than the original and miss its cache. DynamoDB is the cheapest shared cache substrate AWS gives us.

**ElastiCache (Redis) as cache.** Rejected for v0.1: ElastiCache adds a per-hour fixed cost (~$15/month for the smallest viable cluster) and operational surface (cache eviction tuning, network ACLs). DynamoDB on PAY_PER_REQUEST is roughly free at this volume and inherits all the reliability characteristics of the rest of the data plane.

**No `cache_hit` flag in the response.** Rejected: an operator investigating an anomaly cannot tell whether the data is fresh or stale from a flag-less response. The flag costs us a single bool in the envelope and is the cheapest way to surface a load-bearing operational property.

## References

- `services/cost-api/src/panakoes_cost_api/cache.py`
- `services/cost-api/src/panakoes_cost_api/cost_explorer.py`
- `infra/dev/admin-state/main.tf` (the cache table)
- `docs/design/admin-dashboard-tier-2-3.md`
- `docs/design/tier-2-3-implementation-plan.md` (Phase 1.1-1.3)
