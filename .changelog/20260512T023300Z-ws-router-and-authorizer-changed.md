---
category: Changed
---

- `docs/runbooks/streaming-websocket-smoke.md`: rewritten for the JWT-required era. New Step 3 mints a smoke-test JWT via `aws secretsmanager get-secret-value` + a tiny Python `jwt.encode` against `panakoes-dev/jwt-signing-secret`; new Step 4 passes it via `?token=<jwt>` query string (with header-based alternative documented); Step 5 verifies the audio-frame SQS message AND the new streaming-sessions DynamoDB row instead of the prior five-stub-fanout shape. Authorizer-rejection diagnostics added (check `/aws/lambda/panakoes-dev-streaming-ws-authorizer` for WARN `reason` field). Failure-mode taxonomy documented inline (missing-token, malformed header, bad signature, expired, wrong iss/aud, malformed JWT, missing required claim, config-error) so an operator hitting a 401 has the full classification in one place.
