---
category: Fixed
---

- `infra/api-gateway-ws`: streaming-router gains kms:GenerateDataKey on the consolidated app-data CMK; the pooled frame queues are SSE-KMS encrypted with it and every frame send failed KMS AccessDenied after the SQS-level grant was fixed.
