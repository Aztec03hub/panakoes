---
category: Fixed
---

- `services/health-aggregator`: updated test suite for PR #344 drift -- wired `cloudwatch` parameter into `_make_aggregator`, added `admin-api`/`cost-api` to registry assertion, added `detail_for` to `StubAggregator`, fixed DynamoDB Local health-check command (`wget` -> `curl`), and added coverage tests for `CloudWatchClient`, `LogsClient`, OPTIONS endpoints, and `get_aggregator` dependency (92% coverage, was 74%).
