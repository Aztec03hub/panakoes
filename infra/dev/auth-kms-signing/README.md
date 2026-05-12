# infra/dev/auth-kms-signing

Asymmetric AWS KMS key for the auth service's RS256 JWT signing path (ADR-041 phase 1). The auth service signs JWTs by calling `kms:Sign` on this key; the private key material lives inside KMS and the auth service never holds it.

## What this module provisions

- `aws_kms_key.jwt_signing`: an `RSA_2048` asymmetric CMK with `key_usage = SIGN_VERIFY`. Rotation is OFF (AWS does not support automatic rotation on asymmetric keys); manual rotation procedure is documented below.
- `aws_kms_alias.jwt_signing`: alias `alias/panakoes-dev-jwt-signing` pointing at the key. The auth service references the alias so a key rotation is a Terraform-only re-point with no application restart.

## Apply

```bash
cd infra/dev/auth-kms-signing
terraform init
terraform plan
terraform apply
```

Outputs:

- `jwt_signing_key_id`, `jwt_signing_key_arn`, `jwt_signing_key_alias`.

## Wiring into the auth service

1. Apply this module. Capture the alias and key id.
2. In `infra/dev/iam/`, grant the auth task role `kms:Sign` + `kms:GetPublicKey` + `kms:DescribeKey` on this key's ARN. (Done in this PR's iam diff.)
3. In `infra/dev/ecs/`, set `AUTH_JWT_ALGORITHM=RS256` and `AUTH_JWT_KMS_KEY_ID=alias/panakoes-dev-jwt-signing` on the auth task definition.
4. Deploy a fresh auth image (no application code changes needed; the runtime picks up the env var on next deploy).

## Phase 1 vs phase 2

Phase 1 (this PR): the module is provisioned but the auth service stays on HS256 by default. Setting `AUTH_JWT_ALGORITHM=RS256` is the opt-in switch; phase 2 flips that default and removes HS256.

## Rotating the key

Because rotation is manual on asymmetric KMS keys, the procedure is:

1. Apply a second key (rename the existing one to `-deprecated`, add a new one as the primary).
2. Dual-publish: configure the auth service to publish BOTH keys in its JWKS document for one full TTL window (10 minutes by default).
3. Flip the auth service's `AUTH_JWT_KMS_KEY_ID` to the new key id; restart.
4. Wait for the longest valid JWT to expire (1 hour by default).
5. Destroy the old key (the 30-day deletion window gives a rollback path).

## Cost

A single asymmetric KMS key costs $1/month plus $0.03 per 10,000 signing operations. At our dev sign rate (tens of signs per day at most), the marginal cost is dominated by the flat $1/month.
