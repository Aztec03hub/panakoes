---
category: Added
---

- `.github/workflows/auto-rebase-prs.yml`: after every push to `main` (and on `workflow_dispatch`), sweep every open PR and call the GitHub Update Branch API to perform a server-side rebase. Filters by `mergeStateStatus` (DIRTY or BEHIND only) and skips Dependabot BEHIND PRs (Dependabot self-rebases). Posts a comment on real-conflict (HTTP 422) PRs and continues the sweep. Uses the default `GITHUB_TOKEN`; concurrency `auto-rebase-prs` with `cancel-in-progress: true` so the latest main wins if two pushes land in quick succession. Eliminates the DIRTY-state cascade that previously required manual rebase after each sibling PR merge.
