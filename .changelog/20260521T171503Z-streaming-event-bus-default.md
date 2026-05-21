---
category: Fixed
---

- `infra/api-gateway-ws`: default `streaming_event_bus` is now `panakoes-dev` (was `default`). The gpu-spawner's SQS rule lives on the custom `panakoes-dev` bus, so the previous default silently published session-connecting events into the void on every Terraform apply that touched this module. Manual `aws lambda update-function-configuration` was being used to override; this codifies the right value.
