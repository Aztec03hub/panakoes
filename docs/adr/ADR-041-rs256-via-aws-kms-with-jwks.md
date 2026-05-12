# ADR-041: RS256 JWT Signing via AWS KMS with a Live JWKS Endpoint

## Status

Accepted.

Supersedes the slice-2 plan sketched in [ADR-022](ADR-022-jwt-hs256-then-rs256.md). ADR-022 said "RS256 with an RSA key pair held by the auth service"; this ADR refines that to "RS256 with the private key inside AWS KMS, the auth service signs via `kms:Sign` and never holds key material."

## Context

The auth service today signs JWTs with HS256 and a shared secret (ADR-022). Every consumer holds the same secret, which means the blast radius of a leak is "anyone with the secret can mint tokens." That is acceptable while the only consumers are first-party microservices we own, but it does not survive contact with:

1. **The Plaud-killer wearable backend** (separate trust domain; we want a hardware device validating tokens without ever holding the signing key).
2. **A second internal service team** consuming our tokens in a non-trusted context.
3. **Any future federated identity flow** where third parties verify our tokens against a published JWKS document.

The standard production posture is asymmetric signing: the auth service holds a private key, verifiers fetch the public key from a JWKS endpoint. Verifiers cannot forge tokens; rotation is a single `kid` flip with no shared-secret distribution problem.

The decision below refines ADR-022 in one specific direction: **the private RSA key never lives on the auth service**. It is generated and held inside AWS KMS. The auth service signs by calling `kms:Sign`. This eliminates the entire class of "auth service container compromised -> private key exfiltrated" failures.

## Decision

### Signing path

- The auth service supports two signing algorithms, selected by `AUTH_JWT_ALGORITHM`:
  - `HS256` (default; phase 1 here): unchanged from ADR-022.
  - `RS256` (opt-in today; phase 2 default): signs via AWS KMS `kms:Sign` against an `RSA_2048` asymmetric CMK with `key_usage = SIGN_VERIFY`.
- When `AUTH_JWT_ALGORITHM=RS256`, the env requires `AUTH_JWT_KMS_KEY_ID` (an alias or key ARN). The service refuses to boot if it is missing (`config.ts` `refine()`).
- The JWT header carries a `kid` so consumers can match the JWKS entry without trial-decryption. The `kid` is the UUID tail of the KMS key ARN (stable across alias renames).

### JWKS endpoint

- `GET /.well-known/jwks.json` is live in both modes:
  - HS256: returns `{"keys": []}` (HMAC has no public key); contract preserved so JWKS clients can target the URL today.
  - RS256: returns `{"keys": [{kty: "RSA", use: "sig", alg: "RS256", kid, n, e}]}`. The public key is fetched once from KMS via `kms:GetPublicKey` and cached in-process for 10 minutes.
- `Cache-Control: public, max-age=600` is set so downstream clients with their own caches stay aligned with the auth service's TTL.

### Infrastructure

- New Terraform module: `infra/dev/auth-kms-signing/` provisions exactly one resource: an `aws_kms_key` with `customer_master_key_spec = RSA_2048`, `key_usage = SIGN_VERIFY`, alias `alias/panakoes-dev-jwt-signing`, and a 30-day deletion window.
- `infra/dev/iam/main.tf` grants the auth task role `kms:Sign + kms:GetPublicKey + kms:DescribeKey` on the key ARN. Resource-scoped, no wildcard.
- `infra/dev/ecs/main.tf` injects `AUTH_JWT_ALGORITHM` (default `HS256`) and conditionally `AUTH_JWT_KMS_KEY_ID` (only when set) into the auth container env. Flipping production to RS256 is a two-variable Terraform change.

### Python validator path (`panakoes-auth-client`)

- `JwtValidator.from_jwks_url(url, issuer, audience)` builds a JWKS-backed RS256 validator. Cache: 10-minute TTL, indexed by `kid`. Fetcher is injectable for tests.
- `from_env()` chooses HS256 vs RS256 based on whether `JWT_PUBLIC_JWKS_URL` is set. No flag day; consumers opt in per service.
- Algorithm is locked to RS256 in JWKS mode: HS256 tokens cannot be JWKS-validated, so accepting them would be a downgrade hole.

## Consequences

### Positive

- **Private key never leaves KMS.** A compromised auth service container can call `kms:Sign` while the IAM grant exists, but cannot exfiltrate the key. Revoking signing capability is one IAM update.
- **Verifiers never hold the signing key.** Adding a new verifier service (wearable backend, third party) is a JWKS URL configuration change; no secret distribution.
- **Rotation is clean.** Manual: provision a second key, dual-publish in JWKS, flip the auth service's `AUTH_JWT_KMS_KEY_ID`, retire the old key after the longest-valid JWT expires.
- **No code change is required to keep HS256 working.** The opt-in is two env vars; existing tests pass unchanged. Phase 2 (flipping the default) is a config flip and a docs update.

### Negative

- **KMS call latency** is added to every sign. AWS publishes `kms:Sign` p50 latency at single-digit milliseconds in the same region; for the auth service (which signs once per sign-in / sign-up, not per request) this is negligible. The wearable backend will call `kms:Verify` only on signing key rotation; verification stays in-process via the cached public key.
- **Cost.** $1/month per asymmetric KMS key plus $0.03 per 10,000 sign operations. At dev sign volumes the marginal cost is the flat $1/month.
- **Rotation is manual.** AWS does not support automatic rotation on asymmetric CMKs. The procedure is documented in `infra/dev/auth-kms-signing/README.md`.
- **One more remote-state dependency** in `infra/dev/iam/`. Worth it because the auth task role needs the KMS key ARN, and threading it through `var`s instead would couple `auth-kms-signing` apply order to `iam` apply.

### Phase 1 vs phase 2 migration timeline

**Phase 1 (this PR):**
- Ship the RS256 path alongside HS256. Default stays HS256.
- Provision the KMS key in dev. Wire the IAM grant. Wire the env-var plumbing.
- JWKS endpoint serves the (empty-by-design) HS256 document today; the moment `AUTH_JWT_ALGORITHM=RS256` is set, the document populates from KMS.
- Python validators gain `from_jwks_url`. No services flip yet.

**Phase 2 (separate PR; target: after the wearable backend has at least one Python verifier consuming JWKS in dev):**
1. Flip `AUTH_JWT_ALGORITHM` to `RS256` in `infra/dev/ecs` (auth service starts signing RS256).
2. Wait for the longest-valid HS256 JWT to expire (1 hour by default).
3. Flip each Python verifier service to JWKS mode by setting `JWT_PUBLIC_JWKS_URL`. Verify each service independently; rollback per service.
4. Delete `AUTH_JWT_SECRET` and the `jwt-signing-secret` Secrets Manager entry once all verifiers are off HS256.

**Phase 3 (separate PR; production rollout):**
- Apply the `auth-kms-signing` module in the production AWS account.
- Repeat phase 2 in production with the same gates.
- Deprecate the HS256 code path in `services/auth/src/auth/jwt.ts` once production is on RS256 and stable for one week.

The phases are explicit so the migration can be paused at any stage without leaving the system in a non-functional state. If phase 2 surfaces an issue (KMS latency outlier, downstream verifier bug), the rollback is `terraform apply` with `auth_jwt_algorithm = HS256`; no schema migration, no data backfill.

## References

- ADR-022: original HS256-then-RS256 decision; this ADR refines the RS256 endgame.
- ADR-039: auth DB split-credential model; provides the operational pattern for "key material lives outside the service" that this ADR extends to JWT signing.
- `services/auth/src/auth/jwt.ts`, `services/auth/src/auth/kms-signer.ts`, `services/auth/src/auth/jwks.ts`: implementation.
- `services/auth-client/src/panakoes_auth_client/jwks.py`, `services/auth-client/src/panakoes_auth_client/validator.py`: Python verifier path.
- `infra/dev/auth-kms-signing/`: the KMS key module.
- AWS docs:
  - https://docs.aws.amazon.com/kms/latest/developerguide/asymmetric-key-specs.html (key spec catalogue)
  - https://docs.aws.amazon.com/kms/latest/developerguide/rotate-keys.html#rotating-keys-asymmetric (manual rotation procedure)
- RFC 7517 (JWK) + RFC 7518 (JWS algorithms) for the JWKS document shape.
