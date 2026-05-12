# Streaming WebSocket smoke test

## Purpose

Validate that the dev streaming WebSocket API (`panakoes-dev-streaming-ws`) accepts authenticated client connections, dispatches frames on the right route key, hands them to the streaming-router Lambda, and disconnects cleanly. This runbook is the canonical procedure for the first deploy and for every subsequent change to `infra/dev/api-gateway-ws/` (route additions, authorizer changes, integration target swaps).

As of the ws-router-and-authorizer PR, `$connect` requires a valid panakoes-issued HS256 JWT. The token rides as either an `Authorization: Bearer <jwt>` header OR a `?token=<jwt>` query string parameter (browsers cannot set custom headers on the WebSocket handshake, so the query-string path is the canonical browser-mic shape). The header wins when both are present.

It is intentionally GPU-free: the smoke confirms only the gateway plumbing. Real Whisper inference is out of scope here; that lives in the gpu-spawner + transcriber-stream services and has its own smoke runbook (`gpu-ami-bake.md` and a future `streaming-session-end-to-end.md`).

## When to use this runbook

- After a fresh apply of `infra/dev/api-gateway-ws/` (including the first apply).
- After any change to the WebSocket route catalogue (adding or renaming a route key).
- After bumping `streaming_router_image_tag` or `ws_authorizer_image_tag` (rolls a new container image into the Lambda).
- After any change to `services/streaming-router/` or `services/ws-authorizer/` whose container image has been re-baked.
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

### Step 3: Mint a smoke-test JWT

`$connect` now requires a valid HS256 JWT. Mint one against the dev `jwt-signing-secret`. The token's `iss` and `aud` MUST match the values pinned in `infra/dev/api-gateway-ws/variables.tf` (default `https://auth.panakoes.com` and `panakoes-api`).

```bash
JWT_SECRET=$(aws secretsmanager get-secret-value \
  --profile panakoes-admin \
  --region us-east-1 \
  --secret-id panakoes-dev/jwt-signing-secret \
  --query SecretString --output text)

export SMOKE_JWT=$(python3 -c "
import os, time, jwt
now = int(time.time())
print(jwt.encode({
    'sub': 'smoke-user',
    'iss': 'https://auth.panakoes.com',
    'aud': 'panakoes-api',
    'iat': now,
    'exp': now + 300,
    'tenant_id': 'smoke-tenant',
    'role': 'user',
}, os.environ['SECRET'], algorithm='HS256'))
" SECRET="$JWT_SECRET")
echo "Token: ${SMOKE_JWT:0:40}..."
```

### Step 4: Connect with JWT, send one frame per route key, disconnect cleanly

Pass the JWT via the `?token=` query string (matches the browser-mic capture path). Send three frames covering `audio-frame`, `transcript-request`, and the `$default` fallback via an unknown action, then close. `timeout 10` is the safety net.

```bash
printf '{"action":"audio-frame","data":"AAAAAAAAAAAAAAAAAAAAAA=="}\n{"action":"transcript-request","data":{}}\n{"action":"unknown-route","data":{}}\n' \
  | timeout 10 websocat "${WS_INVOKE_URL}?token=${SMOKE_JWT}"
# Expected: exit 0, no `WebSocketError` printed to stderr.
```

If you instead get `WebSocketError: Received unexpected status code (401 Unauthorized)`, the authorizer rejected the token. Check `aws logs tail /aws/lambda/panakoes-dev-streaming-ws-authorizer --since 5m` for the `reason` field on the WARN line.

Alternative shape (header-based, useful for non-browser clients):

```bash
printf '{"action":"audio-frame",...}\n' \
  | timeout 10 websocat -H "Authorization: Bearer ${SMOKE_JWT}" "$WS_INVOKE_URL"
```

If you see `WebSocketError: Received unexpected status code (500 Internal Server Error)` on `$connect`, jump to the Troubleshooting section.

### Step 5: Confirm audio-frame messages landed

Poll the frame queue. The streaming-router now ONLY writes to SQS on the `audio-frame` route, not on every route (unlike the prior stub which fanned everything to SQS). A clean run produces exactly one message: the single `audio-frame` you sent.

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
  | tr '\t' '\n' | jq -r '.session_id'
done
```

Expected output (one line, matching the connection_id API Gateway issued):

```
<connection-id-string>
```

The streaming-router also writes a session row to `panakoes-dev-streaming-sessions` on `$connect` and updates it on `$disconnect`. Verify:

```bash
aws dynamodb scan \
  --profile panakoes-admin \
  --region us-east-1 \
  --table-name panakoes-dev-streaming-sessions \
  --max-items 5 \
  --query 'Items[].{sid:session_id.S,status:status.S,uid:user_id.S}' \
  --output table
```

Expected: a row with `user_id = smoke-user`, `status = disconnected` (transitioned from `connecting` once the close fired).

### Step 6: Inspect access logs (optional but recommended)

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

Two Lambda log groups carry per-invocation lines:

- `/aws/lambda/panakoes-dev-streaming-router` carries one JSON line per WebSocket event with the dispatched route and any side-effect failure.
- `/aws/lambda/panakoes-dev-streaming-ws-authorizer` carries one WARN line per rejected $connect with the `reason` field.

## Verification

The smoke run passes if and only if:

1. Step 4 exited 0 with no `WebSocketError` on stderr.
2. Step 5 produced exactly one queue message (the audio-frame) and a session row in DynamoDB with the smoke user_id.
3. Step 6 access log shows `status: "200"` on every event line and non-empty `integrationStatus`.
4. The authorizer log group contains no WARN entries for the smoke window (no rejected connects).

If any of these fail, treat the deploy as broken until the failure is understood; do not advance the runbook to declare success.

## Lambda authorizer JWT shape

The `$connect` Lambda authorizer (`services/ws-authorizer/`) validates the same Panakoes-issued JWT every HTTP API consumer carries. Shape locked by `services/auth-client/src/panakoes_auth_client/claims.py`:

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

How the authorizer receives the token on a WebSocket `$connect`:

1. `Authorization: Bearer <jwt>` header (preferred when the client controls headers; node clients, server-to-server, etc.).
2. `?token=<jwt>` query string parameter (the browser-mic path; browsers cannot set custom headers on the WebSocket handshake).

API Gateway's authorizer resource is configured with TWO `identity_sources` (`route.request.header.Authorization` and `route.request.querystring.token`), so a request missing both is 401'd at the gateway BEFORE the Lambda fires (saves cost on unauthenticated probes).

The authorizer returns `{"isAuthorized": true, "context": {"user_id": "...", "tenant_id": "...", "role": "..."}}` on success; the streaming-router reads that context map from `event.requestContext.authorizer.lambda.*` on `$connect`.

Failure-mode taxonomy (every case collapses to `{"isAuthorized": false}` with a WARN log line carrying the `reason` field, never echoed to the client):

- `missing-token` (no header AND no query param)
- `Authorization` header present but not `Bearer <token>`
- bad signature (signed with the wrong secret)
- expired (`exp` in the past)
- wrong issuer or audience
- malformed JWT (not three base64 segments)
- missing required claim (sub, iss, aud, iat, exp)
- `config-error` (Lambda boot couldn't read JWT_SECRET; surfaces as 401 not 500 by design)

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
