# services/ws-authorizer

API Gateway v2 WebSocket Lambda authorizer for the panakoes streaming WebSocket API
(`panakoes-dev-streaming-ws`). Validates the caller's HS256 JWT on `$connect` and
emits an `isAuthorized` decision plus a `context` map (user_id, tenant_id, role)
that the downstream streaming-router Lambda consumes via
`event["requestContext"]["authorizer"]["lambda"]`.

## JWT shape

Tokens are issued by the panakoes Auth service. Locked shape (see
`services/auth-client/src/panakoes_auth_client/claims.py`):

| Claim | Required | Notes |
| --- | --- | --- |
| `sub` | yes | user id, exposed as `context.user_id` |
| `iss` | yes | must equal `JWT_ISSUER` env var |
| `aud` | yes | must equal `JWT_AUDIENCE` env var |
| `iat` | yes | issued-at, unix seconds |
| `exp` | yes | expiration, unix seconds |
| `role` | no  | optional, exposed as `context.role` when present |
| `tenant_id` | no | optional, exposed as `context.tenant_id` when present |

## Identity sources

Browsers cannot set custom headers on the WebSocket handshake, so the
authorizer accepts the token via either of two identity sources, in priority
order:

1. `Authorization: Bearer <jwt>` header (preferred when the client controls
   headers, e.g. node-based clients).
2. `?token=<jwt>` query-string param (browser-mic capture path).

If both are present, the `Authorization` header wins.

## Failure modes

Every failure returns `{"isAuthorized": false}` with no detail surfaced to the
client (API Gateway closes the connection on the client side with a 401
handshake response). Failure taxonomy:

- Missing token (no header AND no query param)
- Malformed `Authorization` header (not `Bearer <token>`)
- Signature verification failed
- Token expired (`exp` in the past)
- Wrong issuer or audience
- Malformed JWT (not three base64-segments)
- Missing required claim (sub, iss, aud, iat, exp)

Reasons are logged via structlog at WARN level with a `reason` field so
operators can debug 401s without echoing the token.

## Environment

| Variable | Source | Notes |
| --- | --- | --- |
| `JWT_SECRET` | Secrets Manager `panakoes-dev/jwt-signing-secret` | resolved at boot via the Lambda secret env var injection |
| `JWT_ISSUER` | `https://auth.panakoes.com` (locked) | |
| `JWT_AUDIENCE` | `panakoes-api` (locked) | |
