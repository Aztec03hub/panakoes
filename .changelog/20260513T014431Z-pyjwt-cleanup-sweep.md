---
category: Security
---

- Complete python-jose to PyJWT migration: regenerated uv.lock for six services (ws-authorizer, health-aggregator, cost-rollup-aggregator, transcribe-worker, cost-api, admin-api) to drop transitive python-jose/ecdsa pulled in through panakoes-auth-client and panakoes-test-helpers path-deps; removed dead `[[tool.mypy.overrides]] module = ["jwt", "jwt.*"]` blocks from session-manager and ingestion-api pyproject.toml (PyJWT ships first-party type stubs via py.typed). Closes the remaining six high-severity Dependabot ecdsa transitive alerts.
