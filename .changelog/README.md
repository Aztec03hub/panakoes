# `.changelog/` per-PR fragments

This directory holds one Markdown fragment per pull request. At release time, `scripts/assemble-changelog.sh` collects every fragment, groups by category, prepends the result to `CHANGELOG.md` under a new version header, and deletes the fragments.

**Why this exists:** the prior pattern (every PR appends to a single `CHANGELOG.md`) caused every sibling PR's merge to mark all other open PRs as `DIRTY`, even with `merge=union` resolving the file content. GitHub auto-merge stalls on `DIRTY` until manual rebase, which dominated PR-backlog churn on the 2026-05-08 drain. Per-file fragments eliminate the shared-write contention entirely.

`merge=union` on `CHANGELOG.md` stays in place as a belt-and-suspenders fallback for any direct edits to the assembled file (e.g. a typo fix on the last release block).

## Fragment format

Every PR adds exactly one file at `.changelog/<UTC-timestamp>-<short-slug>.md`. Timestamp format: `YYYYMMDDTHHMMSSZ` (e.g. `20260512T000038Z`). Slug is kebab-case, derived from the PR topic.

The file is YAML-frontmatter Markdown:

```markdown
---
category: Added
---

- `services/admin`: short user-visible description, terse, no marketing fluff.
```

Allowed `category` values (Keep a Changelog): `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.

Body is one or more Markdown bullets. Voice rules from `CLAUDE.md` apply: no em-dashes, direct, concrete and specific.

## Contributor flow

1. Pick a category from the six above.
2. Pick a slug, e.g. `ses-bootstrap`, `dependabot-triage`, `auth-revocation`.
3. Compute the UTC timestamp: `date -u +%Y%m%dT%H%M%SZ`.
4. Write the file: `.changelog/<timestamp>-<slug>.md` with the frontmatter + body shown above.
5. Commit it alongside your code. The CHANGELOG-check CI gate looks for a `.changelog/*.md` change in the PR diff (or a direct `CHANGELOG.md` change as a fallback).

## Release flow

Run from the repo root:

```bash
# Preview what will be written without touching CHANGELOG.md or fragments.
scripts/assemble-changelog.sh --version v0.2.0 --dry-run

# Actually prepend to CHANGELOG.md AND delete the fragments.
scripts/assemble-changelog.sh --version v0.2.0 --prune
```

Without `--prune`, fragments are left in place so the next caller can reassemble. With `--dry-run`, neither `CHANGELOG.md` nor `.changelog/` is touched.

The assembled block has the shape:

```markdown
## [v0.2.0] - 2026-05-12

### Added
- `services/foo`: ...

### Changed
- `services/bar`: ...
```

Categories appear in the canonical Keep a Changelog order: Added, Changed, Deprecated, Removed, Fixed, Security. Empty categories are omitted. Bullets within a category are ordered by fragment filename (i.e. by UTC timestamp ascending) for stable, reviewable output.

## Skip rules

PRs scoped to `docs/*`, `.github/*`, `scripts/*`, `CLAUDE.md`, `PLANNING.md`, `SCOPE.md`, `CONTRIBUTING.md`, `README.md`, `SECURITY.md`, `LICENSE`, `Makefile`, `.githooks/*`, `.gitignore`, `.pre-commit-config.yaml`, `.gitleaks.toml` are exempt from the gate. They do not require a fragment. The exempt list lives in `.github/workflows/changelog-check.yml`.

## Anti-patterns

- Do not edit an existing fragment that another PR authored. Add a new one.
- Do not commit a fragment without a `category:` line; the assembler will reject it.
- Do not put multiple categories in one fragment file; split into N files.
- Do not put more than one fragment per PR unless the PR genuinely spans multiple categories (rare).
