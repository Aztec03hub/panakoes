# api-index

The root landing page, liveness probe, and friendly 404 for the public
Panakoes HTTP API at `https://api.panakoes.com`.

The public API Gateway (`infra/dev/api-gateway/`) routes every backend
service under `ANY /v1/<service>/{proxy+}`, but it has no route for the
root path, so a bare `GET /` used to return API Gateway's stock
`{"message":"Not Found"}`. That URL is shown in interviews, so the root
must be good. This tiny Lambda fills the gap.

## Behavior

| Route | Browser (`Accept: text/html`) | Otherwise |
|---|---|---|
| `GET /` | Self-contained HTML landing page (dark theme, inline CSS, live auth-health badge, route catalog, GitHub + dashboard links). | JSON index: `{"name","description","status","endpoints",...}`. |
| `GET /health` | JSON `{"status":"ok","service":"api-index"}`. | Same JSON. |
| anything else (`$default`) | Small HTML 404 pointing at `/`. | JSON `{"error":"not_found","hint":"GET / for the route index","path":...}`. |

### Why `/health` does not fan out to backends

`/health` is a liveness probe for the index service itself and is
deliberately cheap: it does no network I/O and never calls a backend.
A root index must stay always-up and fast; coupling its health to every
downstream service would make the front door flap whenever any one
service blips, which is exactly backwards for a page meant to look
solid in a demo. Aggregate cross-service health lives behind
`/v1/health-aggregator/`. The landing page does render a single live
status badge, but that is a client-side same-origin fetch to
`/v1/auth/health` made by the browser, not a server-side fan-out.

### One catalog, two surfaces

The HTML table and the JSON `endpoints` map both render from
`catalog.ENDPOINTS` (`src/panakoes_api_index/catalog.py`) so they cannot
drift. That list mirrors `local.alb_public_services` in
`infra/dev/api-gateway/main.tf`. Adding a public service is a one-line
edit there once the route lands.

## Packaging and deploy

Container-image Lambda, same pattern as `services/ws-authorizer/` and
`services/cost-rollup-aggregator/`. The image is baked by the GitHub
Actions image-bake workflow into the `api-index` ECR repository and the
Lambda + routes are wired in `infra/dev/api-gateway/` (`api-index.tf`):
a `GET /`, a `GET /health`, and a `$default` route, all targeting this
Lambda via an `AWS_PROXY` integration.

## Local development

```bash
cd services/api-index
uv sync
uv run pytest          # 100% coverage gate
uv run ruff check .
uv run mypy src
```

The handler is pure (event dict in, response dict out), so unit tests
build API Gateway v2 events inline and assert on the returned dict.
