# `infra/dev/api-gateway-ws`

Provisions the public-facing AWS API Gateway v2 **WebSocket** API (`panakoes-dev-streaming-ws`) that fronts the streaming transcription pipeline in the dev environment. Separate from `infra/dev/api-gateway/` because a single `aws_apigatewayv2_api` cannot serve both `HTTP` and `WEBSOCKET` protocol types, and because splitting state isolates blast radius across the two transport lanes.

## What this module provisions

- One `aws_apigatewayv2_api` of `protocol_type = "WEBSOCKET"` with `route_selection_expression = "$request.body.action"`.
- Lifecycle routes: `$connect`, `$disconnect`, `$default`.
- App routes: `audio-frame`, `transcript-request`.
- One SQS queue (`panakoes-dev-streaming-ws-frames`) used as the smoke-deploy integration target. Every route forwards the frame metadata into this queue via an `AWS` integration so the WS plumbing can be validated end-to-end before the streaming-router Lambda lands.
- IAM role API Gateway assumes to call `sqs:SendMessage` on the frame queue, scoped to the single queue ARN.
- Module-local KMS CMK + alias for the access log group (mirrors `infra/dev/api-gateway/`).
- CloudWatch log group at `/aws/apigatewayv2/panakoes-dev-streaming-ws` with 30-day retention.
- One `aws_apigatewayv2_stage` (`dev`) with `auto_deploy = true`, default per-route throttling, and structured-JSON access logging.

## What this module intentionally does NOT provision

- **Lambda authorizer.** ADR-022 locks the streaming-WS authorizer as a Lambda that validates the same HS256 JWT the rest of Panakoes uses, via the `panakoes-auth-client` Lambda layer. The Lambda is not yet built, so the `$connect` route ships with `authorization_type = "NONE"`. The follow-up PR that lands the Lambda flips this to `CUSTOM` and adds `authorizer_id`; no other resource in this module needs to change. See `docs/runbooks/streaming-websocket-smoke.md` for the locked JWT claim shape the authorizer will validate.
- **Streaming-router Lambda.** The eventual downstream consumer of routed frames. The smoke deploy puts SQS in its place. When the router lands, the per-route integration flips from `AWS` (SQS) to `AWS_PROXY` (Lambda); the queue either stays as a buffered fan-out behind the router or is deleted in the same PR.
- **WAF association.** API Gateway v2 WebSocket APIs do not support direct WAFv2 association (an HTTP-API-only feature). Edge protection for the WS API will live at CloudFront when the custom domain lands.

## Smoke procedure

See `docs/runbooks/streaming-websocket-smoke.md` for the full procedure. Short version: `websocat wss://<api-id>.execute-api.us-east-1.amazonaws.com/dev`, send one JSON frame per route, poll the SQS frame queue to confirm each route dispatched.

## State

- Backend: `s3://panakoes-tf-state-b291597a/dev/api-gateway-ws/terraform.tfstate`.
- Encrypted with the shared bootstrap CMK + locked via the S3 native lockfile (no DynamoDB lock table).

## Apply

```bash
cd infra/dev/api-gateway-ws
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

Apply order: this module depends on the bootstrap state bucket + KMS key. Nothing else in `infra/dev/` reads from this module yet (the future streaming-router Lambda will consume `frame_queue_arn` via remote state). It can apply at any time after bootstrap.
