"""Single source of truth for the public API route catalog.

Both the HTML landing page and the JSON index render from `ENDPOINTS`
so the two surfaces cannot drift apart. The list mirrors the public
routes provisioned by `infra/dev/api-gateway/main.tf`
(`local.alb_public_services`): one `ANY /v1/<service>/{proxy+}`
catch-all per public service. Only the small, demo-worthy subset of
service health endpoints is surfaced here; the full per-service surface
is documented in each service's README.

Keep this list in sync with `local.alb_public_services` in the
api-gateway Terraform module. A new public service is a one-line edit
here once its route lands.
"""

from __future__ import annotations

from typing import Final

NAME: Final = "panakoes-api"
DESCRIPTION: Final = (
    "Cloud audio capture, transcription, and insights platform by LaFayette Labs"
)
SOURCE_URL: Final = "https://github.com/Aztec03hub/panakoes"
DASHBOARD_URL: Final = "https://admin.panakoes.com"

# The route the HTML page polls for the live status badge. Same origin,
# so no CORS preflight and no external dependency.
HEALTH_PROBE_PATH: Final = "/v1/auth/health"

# Ordered list of (route, description) pairs. Order is intentional:
# auth first (the probe target), then ingestion / query / session which
# are the core capture-to-insight path, then the supporting services.
ENDPOINTS: Final[tuple[tuple[str, str], ...]] = (
    ("GET /", "This index (HTML for browsers, JSON otherwise)."),
    ("GET /health", "Liveness probe for the index service itself."),
    ("ANY /v1/auth/{proxy+}", "Authentication, sessions, and JWT issuance (Better-Auth)."),
    ("ANY /v1/ingestion/{proxy+}", "Audio upload and ingestion record creation."),
    ("ANY /v1/query/{proxy+}", "Read transcripts, summaries, and insights."),
    ("ANY /v1/session-manager/{proxy+}", "Streaming session lifecycle and state."),
    ("ANY /v1/summarization/{proxy+}", "AI summaries over transcribed audio."),
    ("ANY /v1/billing/{proxy+}", "Stripe-backed plans, seats, and usage."),
    ("ANY /v1/admin-api/{proxy+}", "Admin dashboard backend (tenant + ops surface)."),
    ("ANY /v1/cost-api/{proxy+}", "Per-tenant cost rollups and reporting."),
    ("ANY /v1/health-aggregator/{proxy+}", "Cross-service health aggregation."),
)


def endpoints_map() -> dict[str, str]:
    """Return the endpoint catalog as an insertion-ordered dict.

    Python dicts preserve insertion order, so the JSON payload keeps the
    same ordering as the HTML table. Returned fresh each call so callers
    cannot mutate the module-level tuple.
    """
    return {route: description for route, description in ENDPOINTS}
