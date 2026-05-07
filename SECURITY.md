# SECURITY.md: Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Panakoes, please report it privately so we can address it before public disclosure.

**Email:** plafaydev@gmail.com
**Subject line:** `[Panakoes Security] <brief description>`

Include:
- A description of the vulnerability
- Steps to reproduce
- Affected component(s) and version(s) if known
- Your assessment of impact and exploitability
- Whether you intend to publish your finding (we appreciate coordinated disclosure)

We will acknowledge receipt within 72 hours, confirm or dispute the finding within 7 days, and coordinate a fix and disclosure timeline with you.

Please do **NOT** open a public GitHub issue for security vulnerabilities, post details in PR comments, or share via Twitter / social channels until a fix has shipped.

---

## Security Posture

Panakoes is a public open-source project. The repository, history, and discussion are visible to anyone. Our security model assumes a hostile reader.

### Defense Layers

1. **Source control hygiene.**
   - `gitleaks` pre-commit hook scans every commit locally for accidentally-committed secrets.
   - GitHub secret scanning + push protection enabled at the repository level.
   - `.gitignore` excludes all known secret-containing file patterns.
   - All commits to `main` go through PR with required CI checks.

2. **Runtime secrets.**
   - Application secrets read from environment variables sourced from AWS Secrets Manager or SSM Parameter Store.
   - No secrets are hardcoded in source code, config files, or test fixtures.
   - IAM policies grant least-privilege access; each service can only read its own parameter prefix.

3. **Infrastructure as code.**
   - Terraform state stored remotely in S3 with KMS encryption and DynamoDB-based state locking.
   - State files are never committed to the repository.
   - Terraform plans are reviewed in PR before apply.

4. **CI/CD credentials.**
   - GitHub Actions assume AWS IAM roles via OIDC federation.
   - No long-lived AWS access keys exist anywhere in the repo, GitHub Actions secrets, or local machines.
   - Each workflow assumes a role scoped to the minimum permissions it needs.

5. **Authentication and authorization.**
   - Better-Auth issues short-lived JWTs.
   - Sensitive admin actions require step-up MFA re-authentication within a recent time window.
   - All admin actions are recorded in the DynamoDB audit log with actor, action, timestamp, source IP, and parameters.

6. **Audit trail.**
   - Application-level audit log in DynamoDB captures user actions, admin lifecycle actions, billing events, and access patterns.
   - AWS CloudTrail captures API-level events at the AWS service boundary.
   - Combined, the two logs provide end-to-end traceability from "user clicked button" to "AWS API was called."

7. **Dependency management.**
   - GitHub Dependabot monitors Python and TypeScript dependency vulnerabilities; auto-PRs for fixes.
   - GitHub CodeQL runs static analysis on every PR.
   - Pinned dependency versions in lockfiles (`uv.lock` or `poetry.lock`, `pnpm-lock.yaml`).

8. **Network security.**
   - All internet-facing endpoints terminate TLS via AWS-managed certificates (ACM).
   - Internal service-to-service traffic uses VPC private networking; no service is internet-exposed unless intentional.
   - Stripe webhooks are signature-verified using the Stripe webhook secret.

### Threat Model Summary

A more detailed threat model lives in `docs/threat_model.md` (planned). Summary:

- **In scope:** opportunistic attackers scraping the public repo or scanning AWS endpoints, malicious authenticated users attempting privilege escalation, supply-chain attacks via dependencies.
- **Out of scope (initial):** nation-state actors, advanced persistent threats, physical access to AWS data centers (handled by AWS).

### Known Limitations

This is an early-stage project. Known security improvements that are tracked but not yet implemented:

- Per-region multi-AZ HA for streaming GPU (single point of failure in initial release).
- Rate limiting at API Gateway for all endpoints (currently only on admin lifecycle endpoints).
- Formal SOC 2 / PCI-DSS certification (project is built with these standards in mind but not formally audited).

These are explicitly tracked in [`SCOPE.md`](SCOPE.md) under the Phase 2 / Phase 3 backlog.

---

## Cryptographic Standards

- TLS 1.2 minimum on all endpoints (1.3 preferred).
- Passwords hashed via Argon2id (Better-Auth default).
- JWTs signed via the signing secret stored in AWS Secrets Manager.
- KMS-managed keys for at-rest encryption of S3, RDS, DynamoDB.

## Update Cadence

Security patches are prioritized over feature work. Critical vulnerabilities target a 48-hour fix window from confirmation. High-severity targets one week. Medium and low fix on the next regular release.
