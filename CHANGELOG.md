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

### Categories used in this changelog
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability or security-relevant changes
