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

### Categories used in this changelog
- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** for vulnerability or security-relevant changes
