# ADR-022: JWT Signing, HS256 in Slice 1 then RS256 + JWKS in Slice 2

## Status

Accepted.

## Context

The Auth microservice (`services/auth/`) ships in slice 1 of the v0.1 MVP. It issues short-lived JWTs (1-hour expiry) backed by database-managed sessions and exposes `/auth/validate` for other services to confirm session freshness. Other Python services (starting with the ingestion API at `services/ingestion-api/services/ingestion_api/auth.py`) need to validate those JWTs to authorize incoming requests.

Two viable signing schemes exist:

1. **HS256 (HMAC-SHA256, symmetric).** A single shared secret signs and verifies. Operationally simple: every service reads the same secret out of the environment and validates locally. The downside is that every verifier holds the signing key, so the blast radius of a leak is "anyone with the secret can mint tokens."
2. **RS256 (RSA-SHA256, asymmetric).** The auth service holds a private signing key; verifiers fetch the public key from a JWKS endpoint. Verifiers can never forge tokens because they only have the public half. This is the standard production posture and supports key rotation cleanly via `kid`-stamped JWKS entries.

For slice 1 (Phil dogfooding the system in a dev environment, single-tenant, no third-party services consuming our tokens), HS256 is sufficient and saves the engineering cost of standing up a JWKS endpoint plus rotation tooling. For slice 2 and beyond (the wearable backend, any second service team consuming our JWTs in non-trusted contexts), RS256 + JWKS is required.

## Decision

**Slice 1: HS256 with an env-driven shared secret.**

- Auth service signs with `AUTH_JWT_SECRET` (zod-validated to be at least 32 bytes at startup; service refuses to boot otherwise).
- Every consuming Python service reads the same secret from its own env (`AUTH_JWT_SECRET`) and validates locally with `python-jose` or equivalent.
- In production the secret is sourced from AWS Secrets Manager / SSM Parameter Store and injected at runtime. Never committed.

**Slice 2: RS256 with a JWKS endpoint.**

- Auth service holds an RSA key pair; the public key is exposed at a `/.well-known/jwks.json` endpoint (or auth-service-relative equivalent).
- Each verifier fetches and caches the JWKS, validates `kid`, and verifies signatures with the public key.
- Key rotation: publish a new `kid` ahead of cutover, sign with the new key, retire the old `kid` after the longest valid token has expired.
- Migration is a coordinated cut: the auth service starts dual-publishing JWKS while still signing HS256 for one window, then flips to RS256.

## Consequences

**Slice 1:**
- All services must validate via the same shared secret. Adding a new service is a configuration change (set `AUTH_JWT_SECRET` from Secrets Manager), not a code change.
- A leak of `AUTH_JWT_SECRET` compromises the entire token issuance ability. Mitigation: secrets live in AWS Secrets Manager, are never in source, and are rotated on any suspected leak.
- Verifiers and signers must agree on the secret byte-for-byte; the 32-byte minimum is enforced at the signer to prevent weak-secret regressions.

**Slice 2:**
- Introducing the JWKS endpoint adds a new HTTP surface on the auth service plus caching logic in every verifier.
- Every existing Python verifier (today: ingestion API; later: transcription orchestrator, summarization service, billing webhook handler) needs a code change to pull and cache the JWKS instead of reading a shared secret.
- Production-credible posture: a token leak no longer compromises the signing key, and rotation no longer requires synchronizing secret distribution to every consumer simultaneously.
- The slice 1 -> slice 2 cutover is itself a project: dual-publish, monitor, flip, retire old `kid`. Plan a deliberate migration window.

## References

- `services/auth/`, TypeScript implementation; see `services/auth/README.md` for the slice-1 HS256 details and the explicit slice-2 migration note.
- `services/auth/src/`, Better-Auth + Hono + jose handler chain.
- `services/ingestion-api/services/ingestion_api/auth.py`, first Python verifier.
- ADR-005 in `PLANNING.md` (Auth = Better-Auth, JWT-based, RBAC + step-up MFA).
- ADR-020 (public-repo security plan; the secrets-manager and OIDC posture this ADR composes with).
