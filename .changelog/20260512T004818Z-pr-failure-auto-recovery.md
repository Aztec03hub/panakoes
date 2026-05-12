---
category: Added
---

- `.github/workflows/auto-recover-pr.yml`: GHA workflow that watches open PRs for failed checks and either auto-rewrites Conventional-Commits-shaped PR titles, posts diagnostic comments on Trivy / test / terraform-plan failures with parsed top-line error context, or applies triage labels (`needs-conventional-title`, `needs-changelog-fragment`, `needs-trivy-fix`, `needs-test-fix`, `needs-tf-plan-fix`, `codeql-self-trip-ack`). Triggers on `pull_request` (opened, synchronize) plus `check_suite` (completed). Sticky comments via HTML-comment marker so a re-run does not duplicate.
