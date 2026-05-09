# ADR-028: PAT authentication for auto-update-prs cascade

## Status

Accepted. Lived since 2026-05-08.

## Context

When `main` moves, every open PR becomes "behind." With `strict_required_status_checks_policy: true` (which we use, so merges are validated against the latest base), behind-PRs cannot merge until rebased. Manually clicking "Update branch" on each open PR after every merge is the kind of toil that only stays manageable when there are at most one or two open PRs at a time. With ten or more open PRs, it's a full-time job.

The `auto-update-prs.yml` workflow eliminates that toil. On every push to `main`, it iterates open PRs and calls the GitHub `PUT /repos/{owner}/{repo}/pulls/{number}/update-branch` API to server-side-merge `main` into each. The API path is dramatically faster than client-side rebase-and-force-push: about 1.5 seconds per PR versus 8 seconds for a local rebase.

Originally the workflow authenticated to the GitHub API using `${{ secrets.GITHUB_TOKEN }}`, the default token GitHub provides to every workflow run. This is the conventional choice and works for most cases.

It does not work here. Per [GitHub's documented anti-recursion policy](https://docs.github.com/en/actions/security-guides/automatic-token-authentication#using-the-github_token-in-a-workflow), pushes to a repository authored by `GITHUB_TOKEN` do not trigger workflow runs. The intent is to prevent a workflow from triggering itself in an infinite loop. The unintended consequence: when our cascade pushed a "Merge branch 'main'" commit to a PR's branch via the `update-branch` API, the resulting new SHA on that branch had no CI runs associated with it. The PR's prior CI was on the old SHA; the new SHA carried no checks; auto-merge waited forever.

We hit this on 2026-05-08 with seventeen open PRs. After the fourth merge to `main`, the cascade had silently invalidated the CI status of every other open PR. The queue gridlocked. We spent multiple hours diagnosing and unwinding before recognizing the root cause: GITHUB_TOKEN, doing exactly what GitHub documents it should do, was incompatible with our workflow's intent.

## Decision

The `auto-update-prs` workflow authenticates with a fine-grained Personal Access Token (`AUTO_UPDATE_PAT`) instead of `GITHUB_TOKEN`. Pushes authored by a PAT are treated as user-initiated events, which DO trigger workflow runs.

PAT scope:

- Repository access: `Aztec03hub/panakoes` only (single-repo scope).
- Permissions: `Contents` = Read+Write (for the merge commit push), `Pull requests` = Read+Write (for the `update-branch` mutation and any auto-merge re-arm calls).
- Expiration: 90 days.

The PAT is stored as a repository secret. The workflow file references it explicitly in the `env:` block with an inline comment cautioning future readers not to "simplify" it back to `GITHUB_TOKEN`.

Forward direction: when the project graduates beyond solo-OSS use, replace the PAT with a GitHub App's installation token. Apps offer no-expiration auto-rotating tokens, identity-as-service audit trails, and survival across maintainer transitions. The PAT path was chosen because for a solo project it is faster to set up and the security trade-off (a leaked PAT can push to one repo) is contained.

## Consequences

**Positive:**

- The cascade actually works as the docstring claimed: pushes from update-branch trigger CI on the new SHA, auto-merge sees real check status, PRs land when CI greens. The "main moves, everyone else stays in sync" automation is finally end-to-end functional.
- The gridlock failure mode is permanently resolved. We can have arbitrarily many PRs in flight without manual rebase intervention, as long as they don't have real merge conflicts.
- The fix is minimally invasive: one environment-variable substitution in one workflow file. No architectural change required.

**Negative:**

- The PAT expires every 90 days. Without rotation, the cascade silently breaks; PRs go BEHIND main and stay there. `docs/operations/ci.md` documents the symptoms ("PRs going BEHIND with no apparent reason; check whether AUTO_UPDATE_PAT has expired") and the rotation steps. A calendar reminder for the maintainer is the operator-side mitigation.
- The PAT is tied to a personal account. If the account is deactivated, the PAT dies with it. Acceptable for solo OSS; not acceptable for production-team use, where a GitHub App is the right answer.
- A leaked PAT can push to and modify PRs on this repository. Mitigated by fine-grained scope (one repo, two permission categories), short expiration, storage in GitHub Actions secrets only (not in code, not in commit messages, not in CI logs).
- One narrow attack surface: a malicious workflow run that exfiltrates the secret. Mitigated because `auto-update-prs.yml` only fires on `push: branches: main`, which forks and untrusted contributors cannot trigger; the secret is never exposed to PR-event workflows from forks.

## References

- `docs/operations/ci.md`, "Auto-update PR branches" section.
- `.github/workflows/auto-update-prs.yml`, the inline `AUTH IMPORTANT` comment block.
- 2026-05-08 incident: GITHUB_TOKEN cascade silently invalidated CI on 17 open PRs.
- GitHub documentation: [Automatic token authentication](https://docs.github.com/en/actions/security-guides/automatic-token-authentication).
- `feedback_panakoes_lessons.md` memory entry, "auto-update-prs cascade thrash" section.
