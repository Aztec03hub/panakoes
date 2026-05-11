# Streaming WebSocket smoke test

## Purpose

Validate that the dev streaming WebSocket API (`panakoes-dev-streaming-ws`) accepts new client connections, dispatches frames on the right route key, hands them to the downstream consumer, and disconnects cleanly. This runbook is the canonical procedure for the first deploy and for every subsequent change to `infra/dev/api-gateway-ws/` (route additions, authorizer swap-in, integration target swap from stub to streaming-router Lambda).

It is intentionally GPU-free: the smoke confirms only the gateway plumbing. Real Whisper inference is out of scope here; that lives in the gpu-spawner + transcriber-stream services and has its own smoke runbook (`gpu-ami-bake.md` and a future `streaming-session-end-to-end.md`).

## When to use this runbook

- After a fresh apply of `infra/dev/api-gateway-ws/` (including the first apply).
- After any change to the WebSocket route catalogue (adding or renaming a route key).
- After landing the Lambda authorizer PR that flips `$connect` from `authorization_type = NONE` to `CUSTOM`.
- After swapping the integration target from the stub Lambda to the real streaming-router Lambda.
- During incident response when a streaming-session client reports `1006` / `403` / `500` on connect or message dispatch.

## Prerequisites

Run the following on your dev machine before starting:

```bash
# AWS CLI with the panakoes-admin profile (account 659225405128).
aws sts get-caller-identity --profile panakoes-admin --query Arn --output text
# Expected: arn:aws:iam::659225405128:user/phil

# websocat (Rust WebSocket client). Install via uv if missing:
#   uv tool install websocat
websocat --version
# Expected: websocat 1.13.0 or newer.

# jq for parsing SQS message bodies in the verification step.
jq --version
```

Pull the live WS API ID + frame queue URL from Terraform outputs so the smoke commands match the current dev state (do NOT hardcode):

```bash
cd infra/dev/api-gateway-ws
export WS_INVOKE_URL=$(terraform output -raw stage_invoke_url)
export FRAME_QUEUE_URL=$(terraform output -raw frame_queue_url)
echo "WS:    $WS_INVOKE_URL"
echo "Queue: $FRAME_QUEUE_URL"
```

Expected on 2026-05-11:
- `WS_INVOKE_URL = wss://a75u8kj039.execute-api.us-east-1.amazonaws.com/dev`
- `FRAME_QUEUE_URL = https://sqs.us-east-1.amazonaws.com/659225405128/panakoes-dev-streaming-ws-frames`

## Procedure

### Step 1: Purge the frame queue

Smoke-test isolation. If a previous run left messages in the queue, the verification step in Step 4 cannot prove the new run dispatched anything. SQS allows one `PurgeQueue` per 60 seconds, so do this first and wait if the API refuses.

```bash
aws sqs purge-queue \
  --profile panakoes-admin \
  --region us-east-1 \
  --queue-url "$FRAME_QUEUE_URL"
sleep 5
```

### Step 2: Confirm queue is empty

```bash
aws sqs get-queue-attributes \
  --profile panakoes-admin \
  --region us-east-1 \
  --queue-url "$FRAME_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' \
  --output text
# Expected: 0
```

### Step 3: Connect, send one frame per route key, disconnect cleanly

Send three frames (covering `audio-frame`, `transcript-request`, and the `$default` fallback via an unknown action) over a single WebSocket connection, then close. `timeout 10` is the safety net that closes the socket if websocat hangs; close is graceful (RFC 6455 close frame), not a TCP reset.

```bash
printf '{"action":"audio-frame","data":"AAAAAAAAAAAAAAAAAAAAAA=="}\n{"action":"transcript-request","data":{}}\n{"action":"unknown-route","data":{}}\n' \
  | timeout 10 websocat "$WS_INVOKE_URL"
# Expected: exit 0, no `WebSocketError` printed to stderr.
```

If you see `WebSocketError: Received unexpected status code (500 Internal Server Error)` on `$connect`, jump to the Troubleshooting section.

### Step 4: Confirm every route dispatched

Poll the frame queue. The stub Lambda forwards one message per route invocation, so a clean run produces exactly five messages: one each for `$connect`, `audio-frame`, `transcript-request`, `$default` (from the `unknown-route` action), and `$disconnect`.

Drain in two passes because `aws sqs receive-message` defaults to short-poll (max 10 messages per call):

```bash
for pass in 1 2; do
  echo "--- pass $pass ---"
  aws sqs receive-message \
    --profile panakoes-admin \
    --region us-east-1 \
    --queue-url "$FRAME_QUEUE_URL" \
    --max-number-of-messages 10 \
    --wait-time-seconds 5 \
    --query 'Messages[].Body' \
    --output text \
  | tr '\t' '\n' | jq -r '.route + "\t" + .eventType'
done
```

Expected output (order may vary):

```
$connect	CONNECT
audio-frame	MESSAGE
transcript-request	MESSAGE
$default	MESSAGE
$disconnect	DISCONNECT
```

### Step 5: Inspect access logs (optional but recommended)

The access log group `/aws/apigatewayv2/panakoes-dev-streaming-ws` carries one JSON line per WebSocket event. Look for `status = 200` and a non-empty `integrationStatus`:

```bash
aws logs tail \
  --profile panakoes-admin \
  --region us-east-1 \
  --since 5m \
  /aws/apigatewayv2/panakoes-dev-streaming-ws
```

A healthy CONNECT line looks like:

```json
{
  "connectionId": "dOKKTcU2IAMCIaA=",
  "errorMessage": "-",
  "eventType": "CONNECT",
  "integrationLatency": "297",
  "integrationStatus": "200",
  "routeKey": "$connect",
  "status": "200"
}
```

The Lambda execution log group `/aws/lambda/panakoes-dev-streaming-ws-stub` carries a `streaming-ws-stub: {...}` line per invocation. Useful when the access log shows a 200 but the SQS queue receives nothing (means Lambda was invoked but the SQS send failed).

## Verification

The smoke run passes if and only if:

1. Step 3 exited 0 with no `WebSocketError` on stderr.
2. Step 4 produced exactly five queue messages: one CONNECT, one DISCONNECT, and three MESSAGE events with `route` values `audio-frame`, `transcript-request`, and `$default`.
3. Step 5 access log shows `status: "200"` on every event line and non-empty `integrationStatus`.

If any of these fail, treat the deploy as broken until the failure is understood; do not advance the runbook to declare success.

## Lambda authorizer JWT shape (forward-compatibility note)

The Lambda authorizer planned for `$connect` (ADR-022; not yet implemented as of 2026-05-11) will validate the same Panakoes-issued JWT that every HTTP API consumer carries today. The shape is locked by `services/auth-client/src/panakoes_auth_client/claims.py`:

```python
class JwtClaims(BaseModel):
    sub: str                     # owner subject; used to scope the WS session to a user_id
    iss: str                     # MUST equal AUTH_JWT_ISSUER  = "https://auth.panakoes.com"
    aud: str                     # MUST equal AUTH_JWT_AUDIENCE = "panakoes-api"
    iat: int                     # issued-at, epoch seconds
    exp: int                     # expiry, epoch seconds (token rejected if past)
    jti: str | None              # token id; future audit-log + revocation hook
    scopes: list[str]            # coarse-grained scopes (currently always [])
    role: str | None             # "admin" gates Tier 2 / Tier 3 admin routes; not used by streaming
    mfa_step_up_at: int | None   # only relevant to Tier 3 admin ops; ignored by streaming
```

Validator: `services/auth-client/src/panakoes_auth_client/validator.py`. Algorithm: HS256 (rotatable to RS256/JWKS via the `algorithms` parameter; no consumer changes required at that flip).

How the authorizer receives the token on a WebSocket `$connect`: clients pass `?access_token=<jwt>` as a query-string parameter on the initial HTTP-upgrade request. The authorizer's `identitySource` is `route.request.querystring.access_token`. The authorizer Lambda imports `JwtValidator` from the `panakoes-auth-client` Lambda layer, validates the token, and returns an IAM policy that allows `execute-api:Invoke` on the `$connect` route ARN. The validated `sub` is passed downstream to the streaming-router Lambda via the integration's `requestContext.authorizer.sub` field.

Until that authorizer lands:

- `$connect` route's `authorization_type = NONE`.
- Anyone with the URL can connect. Acceptable in dev for a smoke; not acceptable in prod.
- The follow-up PR that lands the authorizer flips one Terraform field and adds the `aws_apigatewayv2_authorizer` + `aws_lambda_function` resources. No route resource changes.

## Rollback

If the smoke fails on a fresh apply and the problem cannot be diagnosed within a debug window, the safest action is to leave the WebSocket API in place (no client traffic uses it yet) and triage on the next workday. There is no client-facing dependency in dev today; rollback is not urgent.

If a future deploy DOES break a working WS (after the streaming-router Lambda lands and clients are using it), revert via:

```bash
cd infra/dev/api-gateway-ws
git revert <bad-commit-sha>
terraform plan
terraform apply
```

Do NOT `terraform destroy` the entire module to roll back a single change. Destroying tears down the SQS queue, the access log group, the KMS CMK (7-day delete window), and the API itself; replacing them takes 5-10 minutes and burns connection-id state.

## Troubleshooting

### `WebSocketError: Received unexpected status code (500 Internal Server Error)` on `$connect`

Three root causes hit during the first deploy (PR #258); check in order.

1. **Stale deployment.** The stage's last auto-deployment is older than the last route or integration change. Force a fresh deployment:
   ```bash
   aws apigatewayv2 create-deployment --profile panakoes-admin --region us-east-1 --api-id $(terraform output -raw api_id) --description "manual redeploy after route change"
   ```
   Then re-run Step 3. The auto-deploy flag on the stage usually does this within seconds; force it manually if the apply finished more than 30 seconds ago and the smoke still 500s.

2. **Lambda permission missing.** Confirm the `AllowAPIGatewayInvoke` statement on the stub Lambda's resource policy is present and its `SourceArn` matches the API id:
   ```bash
   aws lambda get-policy --profile panakoes-admin --region us-east-1 --function-name panakoes-dev-streaming-ws-stub --query 'Policy' --output text | jq '.Statement[0].Condition.ArnLike."AWS:SourceArn"'
   # Expected: "arn:aws:execute-api:us-east-1:659225405128:<api-id>/*/*"
   ```
   If the API id segment does not match the live API id, `terraform apply` again (the resource is recreated when the API id changes).

3. **Route points at an old integration.** If the apply log included `ConflictException: Cannot delete Integration because it is referenced by the following Routes`, Terraform left some routes pointing at integrations that should have been replaced. Repoint manually:
   ```bash
   API_ID=$(terraform output -raw api_id)
   NEW_INTEGRATION_ID=$(aws apigatewayv2 get-integrations --profile panakoes-admin --region us-east-1 --api-id $API_ID --query "Items[?IntegrationType=='AWS_PROXY'].IntegrationId | [0]" --output text)
   for route_id in $(aws apigatewayv2 get-routes --profile panakoes-admin --region us-east-1 --api-id $API_ID --query 'Items[].RouteId' --output text); do
     aws apigatewayv2 update-route --profile panakoes-admin --region us-east-1 --api-id $API_ID --route-id $route_id --target "integrations/$NEW_INTEGRATION_ID"
   done
   ```
   Then `terraform apply` to delete the orphaned integrations and re-run the smoke.

### Queue receives fewer than five messages

Likely Lambda is throwing inside the SQS send-message call. Check `/aws/lambda/panakoes-dev-streaming-ws-stub` for tracebacks. The common cause is the `FRAME_QUEUE_URL` environment variable diverging from the actual queue URL (happens if the queue was renamed in Terraform without re-applying the Lambda). Re-apply the module to refresh the env var.

### `eventType = MESSAGE` but `routeKey = $default` for an action your code expects to match a named route

The route's auto-deployment to the stage did not register the new route key. Force a redeploy as in Troubleshooting case 1, OR confirm the route exists in the API:

```bash
aws apigatewayv2 get-routes --profile panakoes-admin --region us-east-1 --api-id $(terraform output -raw api_id) --query 'Items[].RouteKey' --output text
```

## References

- ADR-011 (Streaming session model, session-spawned GPU): `PLANNING.md` line 75.
- ADR-022 (Streaming transport): in `docs/adr/` once authored; until then, design notes are inline in `infra/dev/api-gateway-ws/README.md` and in `docs/architecture.md` line 110.
- Module: `infra/dev/api-gateway-ws/`.
- JWT validator: `services/auth-client/src/panakoes_auth_client/validator.py` and `claims.py`.
- Sibling HTTP API runbook posture: the HTTP API in `infra/dev/api-gateway/` has its own smoke (gateway-level health probes via each service's `GET /v1/<service>/health`) and does not share routing with this module.
- `feedback_panakoes_lessons.md` (memory) for required-check propagation and post-apply verification discipline.
