# ADR-026: CHANGELOG.md Merge=Union

## Status

Accepted.

## Context

The CHANGELOG-update gate (per CLAUDE.md "CHANGELOG and README" section) requires every meaningful PR to add an entry under `[Unreleased]` in `CHANGELOG.md`. With orchestrator-delegation (ADR-024) running concurrent feature branches, two or more branches routinely append distinct entries to the same `[Unreleased]` section.

Default git merge behavior treats those concurrent appends as conflicting hunks. On rebase to `main` (or on merge), git produces conflict markers like:

```
<<<<<<< HEAD
- Audit library with three backends.
=======
- Worktree convention for parallel agents.
>>>>>>> feat/another-branch
```

The correct resolution is always "keep both," in some order. There is no semantic conflict; both entries belong in the same section. We hit this three times on night two before fixing it. Each instance was mechanical resolution work that broke flow and added zero value, because the resolution was always identical: union the two sides.

Git supports a `merge=union` strategy via `.gitattributes` that performs exactly this operation: when two sides modify the same hunk, git unions both sides into the result with no conflict markers. The strategy is built into git itself and requires no additional tooling.

The risk of `merge=union` is that it can produce semantically wrong results if the two sides overlap meaningfully (for example, if both edited the same existing line in different ways). For CHANGELOG.md specifically, the file is structurally append-only within the `[Unreleased]` section, so the risk is minimal. For source code, the risk is unbounded: silently unioning two divergent edits to the same function would produce code that compiles but does the wrong thing. The strategy must be scoped narrowly.

## Decision

`.gitattributes` declares:

```
CHANGELOG.md merge=union
```

Scope is narrow: this single line, this single file. Concurrent CHANGELOG additions are unioned automatically; conflict markers never appear; rebases proceed cleanly.

The pattern does **not** extend to source code, configuration, or any other file. Source-code merges remain on the default merge strategy. Any proposal to add a second `merge=union` entry in `.gitattributes` requires explicit review and a separate ADR.

## Consequences

**Positive:**
- Concurrent CHANGELOG appends stop generating conflicts during rebase or merge. Three night-two incidents do not recur.
- Authors do not need to coordinate CHANGELOG edits. Each branch adds its entry; git unions them at merge time.
- Wall-clock cost of merging concurrent feature branches drops by however many CHANGELOG conflicts they would have produced.

**Negative:**
- Authors must still ensure their entry lands in the right section (`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`) and matches the existing entry style. The merge strategy unions text; it does not curate the result.
- Order of appended entries is determined by which side is "ours" versus "theirs" at merge time. Not deterministic across reorderings of the merge graph. Acceptable: the human reader does not depend on insertion order within a release section.
- The strategy must remain scoped to CHANGELOG.md. Generalizing it to source files would silently corrupt code at merge time. The narrow scope is the correctness property; it is not optional.

## References

- PR #27, the change that introduced the `.gitattributes` line, after the third night-two CHANGELOG conflict made the pattern obvious.
- `.gitattributes`, the file housing the rule.
- `CLAUDE.md`, "CHANGELOG and README" section, the discipline that produces the concurrent-append pattern in the first place.
- `feedback_panakoes_lessons.md` memory entry, codifies the lesson alongside other night-one and night-two findings.
