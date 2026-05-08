# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project skeleton: directory structure, license, security configs
- `CLAUDE.md` defining project conventions, locked architectural decisions, working modes, and discipline rules
- `README.md` introducing the project and its tech stack
- `.gitignore` covering Python, TypeScript, Terraform, AWS, secrets, and OS artifacts
- MIT license
- Terraform bootstrap module at `infra/bootstrap/` creating remote state backend (S3 bucket with KMS encryption + DynamoDB lock table). Local state for this module only; all other Terraform configurations use the S3 backend it creates.
- Terraform configuration at `infra/global/` setting up GitHub Actions OIDC federation to AWS. Creates an IAM OIDC provider for GitHub's token issuer and an IAM role (`panakoes-github-actions`) that workflows in the `Aztec03hub/panakoes` repo can assume via short-lived credentials. Eliminates the need for long-lived AWS access keys in GitHub Secrets.
- Pre-commit hooks via `pre-commit` framework: gitleaks (secret scanning), trailing-whitespace, end-of-file-fixer, check-merge-conflict, check-added-large-files (max 1MB), check-yaml/json/toml, detect-private-key, mixed-line-ending fix, terraform_fmt and terraform_validate.
- `.gitleaks.toml` extending gitleaks default rules with allowlist for known-safe lock files and AWS docs example credentials.
- GitHub Actions: `gitleaks` workflow (server-side secret scan defense-in-depth), `codeql` workflow (static analysis on `actions` language for now), `changelog-check` workflow (fails PR if source changed without CHANGELOG update; skippable via `skip-changelog` label), `release` workflow (creates GitHub Release on SemVer tag push).
- Dependabot configuration scanning github-actions and terraform ecosystems weekly.
- Pull request template with Conventional Commits change-type checklist.
- Issue templates: bug report (form-based), feature request (form-based), and config redirecting security reports to GitHub Security Advisories.
- Structured agent-run-report convention. Every sub-agent invocation that touches files now emits a report at `.agent-runs/<timestamp>-<slug>.md` with YAML frontmatter (status, files touched, verification metrics) and a markdown body (decisions beyond brief, issues, suggestions, rollback procedure). Reports are gitignored individually; the directory README is the only committed artifact. CLAUDE.md updated to require the report from every Agent invocation and to add a verification step to the orchestrator's post-agent checklist.
- Python test infrastructure scaffolding: `services/_template/` skeleton (FastAPI app, pyproject.toml with uv-managed deps + ruff + mypy strict + pytest-cov configs, structured logging, multi-stage Dockerfile, smoke tests). Repo-root `Makefile` with discovery-based targets for setup, test, lint, typecheck, coverage, and a CI-mirror `check` target. New `.github/workflows/pytest.yml` runs ruff + mypy + pytest with 80% coverage gate per service, dynamically discovering services that have a pyproject.toml. Always-runs `pytest-status` sentinel job lets the workflow be added as a required branch-protection check from day one, before any services exist.
- Auth microservice v0.1 MVP at `services/auth/`. TypeScript + Hono + Better-Auth + Drizzle/Postgres. HS256 JWT signing with env-var secret (RS256 + JWKS deferred to slice 2). Endpoints: GET /health, POST /auth/sign-up, POST /auth/sign-in, POST /auth/sign-out, POST /auth/validate. Integration tests via testcontainers Postgres at 100% coverage on auth paths per ADR-018. Multi-stage Dockerfile targeting Node 22 slim. Pino structured logging.
- `.github/workflows/vitest.yml` for TypeScript service tests with auto-discovery matrix and always-runs sentinel job (mirrors `pytest.yml` pattern).
- Terraform configuration `infra/dev/network/` creating the dev environment VPC + networking primitives via terraform-aws-modules/vpc. VPC CIDR 10.10.0.0/16 across 3 AZs (us-east-1a/b/c), 3 public subnets, 3 private subnets, single NAT gateway in us-east-1a (cost-disciplined for dev; prod should use multi-AZ NAT), Internet Gateway, locked-down default security group, VPC Flow Logs to CloudWatch Logs with 30-day retention. Outputs surface IDs and CIDRs for downstream configs to consume via terraform_remote_state.
- `panakoes-audit` library at `services/audit-lib/`. Structured audit-event library that every Python microservice imports to write events. Three backends: MemoryAuditStore (tests), StdoutAuditStore (local dev), DynamoDBAuditStore (prod, via moto in tests). AuditEvent Pydantic model with validators (action regex, UTC timestamp, required fields). 100% test coverage per ADR-018.

### Fixed
- Auth service CI: pnpm 11 reads built-script approvals from `pnpm-workspace.yaml`'s `allowBuilds` field, not the legacy `pnpm.onlyBuiltDependencies` in package.json. Generated via `pnpm approve-builds --all`. Allowlists `@biomejs/biome`, `cpu-features`, `esbuild`, `ssh2` (the latter two are transitive deps from testcontainers). Bumped CI Node version from 22 to 24 (active LTS). Sanitized vitest coverage artifact name (forward slashes disallowed by upload-artifact).
- Auth service `package.json` formatted with biome's preferred single-line `onlyBuiltDependencies` array (the multi-line form failed biome's formatter check in CI).

### Changed
- Bumped AWS Terraform provider from `~> 5.0` to `~> 6.0` in both `infra/bootstrap` and `infra/global`. Replaces the two Dependabot PRs that were closed earlier pending deliberate review of v5-to-v6 breaking changes. Both modules `terraform validate` clean against v6; our resources are simple primitives (S3, KMS, DynamoDB, IAM, OIDC) not affected by the v6 breaking-change list. Lock files regenerated.

### Categories used in this changelog
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability or security-relevant changes
