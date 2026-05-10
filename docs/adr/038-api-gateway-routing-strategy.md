# ADR-038: API Gateway routing strategy: proxy default with explicit overrides

## Status

Accepted (2026-05-10).

## Context

`infra/dev/api-gateway/` originally shipped with a flat 25-route table, one explicit `aws_apigatewayv2_route` per public endpoint, hand-curated in `local.routes` and filtered by which backend services had landed an NLB listener ARN in remote state. The shape was correct for a 3-service prototype and incorrect for a 7+ service trajectory:

- Every new endpoint inside any backend service required a coordinated PR to the api-gateway module. Two-repo coupling on a per-endpoint basis.
- The route table drifted from the actual service surface as soon as a service team added an endpoint without remembering to update infra.
- API Gateway HTTP API v2 caps routes per API at 300; the explicit-everywhere approach burns the budget at maybe 50 routes across 7 services.

PR #197 (services-first refactor) deliberately preserved the 25-route table because the simplification was a meaningful contract change, not a refactor. The decision was deferred and captured in `panakoes_api_gateway_proxy_route_simplification_deferred.md` with a full pros/cons matrix between explicit-everywhere and pure-proxy. Phil reviewed the matrix on 2026-05-10 and chose a third shape: proxy catch-all per service as the default, with explicit overrides ON TOP of the catch-all when (and only when) per-route policy is required. Internally this is the (c+) shape. This ADR records the decision and the implementation pattern.

## Decision

The api-gateway module's routing surface follows two layered rules:

1. **Default: per-service proxy catch-all.** Every service whose NLB listener ARN appears in the discovered map (`local.service_nlb_listener_arns`) automatically gets a single route at `ANY /v1/<service>/{proxy+}`. The route forwards to a per-service `HTTP_PROXY` integration that strips the `/v1/<service>/` prefix via `request_parameters = { "overwrite:path" = "/$request.path.proxy" }`. The backend service sees canonical paths (e.g. `/health`, `/sign-up`) and owns its own routing internally.

2. **Explicit overrides where policy demands it.** Routes that need per-route throttling, a distinct authorizer, or a distinct CloudWatch dimension are layered on top of the catch-all via `local.explicit_overrides`. API Gateway HTTP API v2's matcher prefers the more-specific route at request time, so an override like `POST /v1/auth/sign-up` wins over `ANY /v1/auth/{proxy+}` for that exact method-and-path tuple while every other `/v1/auth/*` request flows through the catch-all.

Both shapes target the SAME backend NLB. They go through DIFFERENT integrations because the path-rewrite differs:

- The proxy integration rewrites to `/$request.path.proxy` (the captured greedy segment).
- The override integration rewrites to a literal stripped path baked into the override's `backend_path` field (e.g. `/sign-up`).

Per-route `request_parameters` are not exposed on `aws_apigatewayv2_route` in HTTP API v2, so the rewrite has to live on the integration. One integration per override is the trade we accept; the alternative is a single integration with a parameter mapping that handles both cases, which API Gateway's parameter-mapping grammar cannot express.

Per-route throttling is configured via `dynamic "route_settings"` blocks on `aws_apigatewayv2_stage.main`, iterating the same `explicit_overrides` map, so adding a new throttled override is a one-line change and the stage picks it up automatically.

## Consequences

**Positive.**

- **Service teams own their API surface end-to-end.** Adding a new endpoint inside a service is a service-code change with zero infra PR. Two-repo coupling drops to one-repo by default.
- **Per-route policy is still available** for routes that genuinely need it. Throttling, authorizer overrides, distinct CloudWatch dimensions all attach via `explicit_overrides`. The mixed shape is intentional, not a compromise.
- **Per-route metrics on overrides remain meaningful.** The route key is the CloudWatch dimension, so `POST /v1/auth/sign-up` shows up as its own series in the AWS/ApiGateway namespace.
- **Route quota stops being a constraint.** 7 services + a dozen overrides occupy ~20 routes against a 300-route ceiling.
- **Public URLs follow a predictable pattern.** `/v1/<service>/<service-internal-path>`. Backend services mount routes at their canonical path (typically root for custom routes; SDK conventions like Better-Auth's `/api/auth/*` are preserved internally and surface through the proxy as `/v1/auth/api/auth/*` if that's what the SDK requires).

**Negative.**

- **The route surface is no longer self-documenting from `terraform state list` alone.** Newcomers have to combine the explicit-overrides map with each service's internal routing to know what endpoints exist. Mitigation: each service exposes its own OpenAPI document at `/openapi.json` (or framework equivalent); a future build step aggregates them into the public API spec.
- **Explicit-override routes carry a hardcoded `backend_path`.** If the backend renames a handler (`/sign-up` to `/signup`), both the service code AND the override entry have to change. The drift risk is small because overrides are rare by design and the mismatch fails loudly with a 404 in the integration test.
- **Two integrations per service in the worst case** (one proxy + one per override). For the first cut, the auth service owns one proxy + two overrides = three integrations. Acceptable; well under the per-API integration quota.
- **WAF rules that key on path patterns** still work (the `/v1/<service>/*` prefix is stable), but rules that key on a specific endpoint must reference the override route key explicitly. Same trade-off as any proxy gateway.

## Alternatives considered

**Explicit routes everywhere (the original shape).** Rejected. Every new service endpoint becomes a two-repo PR forever. The 25-route table at 3 services was already 25 lines of declarative friction; at 7 services it would be 60+. Self-documentation from `terraform state list` is real value, but not worth the forever-tax on service teams.

**Pure proxy, no overrides.** Rejected. Per-route throttling, authorizer overrides, and per-route CloudWatch dimensions disappear. For routes like `POST /v1/auth/sign-up` that need anti-enumeration rate limits, the only fallback is in-service throttling, which puts the rate-limit logic in every service and loses the gateway's request-shedding-before-it-reaches-the-VPC behavior. Not worth it.

**REST API v1 migration to access per-route parameter overrides.** Rejected. REST API v1 is roughly 3.5x the per-million-request cost of HTTP API v2, adds cold-start latency, and exposes a larger surface (request validators, models, API keys with usage plans) we don't need. The two-integration-per-override pattern in HTTP API v2 is a smaller cost than the migration tax.

**Single integration with parameter-mapping that handles both proxy and override cases.** Considered. AWS's `request_parameters` mapping grammar supports `$request.path.proxy` references but does not support conditional rewrites or fallbacks. A single integration cannot express "if route key has `{proxy+}` then use the captured segment, else use a literal path." Two integrations is the correct decomposition.

## References

- `infra/dev/api-gateway/main.tf` (the (c+) shape implementation: `local.proxy_services`, `local.explicit_overrides`, `service_proxy` + `service_override` integration / route resources, dynamic `route_settings` on the stage).
- `infra/dev/api-gateway/README.md` (operator guide: how to add an explicit override).
- `panakoes_api_gateway_proxy_route_simplification_deferred.md` (the deferred decision this ADR resolves).
- ADR-035 (broader story on API Gateway HTTP API v2 selection).
- AWS docs: API Gateway HTTP API parameter mappings (`overwrite:path`, `$request.path.<name>`).
