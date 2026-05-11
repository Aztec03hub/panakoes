"""Panakoes health-aggregator service (Tier 1 of the admin dashboard).

Replaces the bundled `services/admin/static/dashboard/health.json` mock
with live data sourced from ECS, ELBv2 target groups, and CloudWatch
Logs. The admin SPA's `USE_LIVE_HEALTH_AGGREGATOR` feature flag swings
to this service's `/v1/health-aggregator/health` and
`/v1/health-aggregator/services/<name>` routes once the service is
deployed and gateway-wired.
"""
