---
category: Fixed
---

- `infra/api-gateway-ws`: lock down two sources of drift on the WS authorizer that broke every browser `$connect` after every CI Terraform apply. (1) `identity_sources` is now `["route.request.querystring.token"]` only because API Gateway v2 WebSocket treats the list as AND, and browsers cannot attach a custom `Authorization` header on a WS upgrade. (2) The authorizer Lambda's `environment` block now has `ignore_changes = [environment]` so the out-of-band `JWT_SECRET` injection from Secrets Manager (per runbook) is not nuked on every apply.
