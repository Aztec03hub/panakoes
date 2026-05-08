# Local Dev Troubleshooting Runbook

## When to use this runbook

Reach for this runbook when local development tooling misbehaves: setup steps fail, a hook blocks a commit, a dependency manager refuses to install, tests hang, Terraform stalls on a lock, or an agent run reports an environment error rather than a code defect. These are not production incidents; they are friction items, and the rule is to fix them in the runbook so the next person (or agent) does not pay the same cost.

If a runtime system is broken in the cloud, see `incident-response.md` instead. If foundational data or infrastructure needs recovery, see `disaster-recovery.md`.

## Prerequisites

- Bash on Linux or WSL2 (Phil's primary dev environment is WSL2 on Windows).
- `git`, `gh`, `make`, and a C toolchain (for native dependencies that uv or pnpm may build).
- Repo cloned at `/mnt/c/Users/plafayette/Documents/Facebook/panakoes` (or an agent worktree path).

## Procedure

Each section below is independent. Pick the section matching the failure; do not run them as a sequence.

### 1. nvm / Node version mismatch

Symptom: `node --version` returns the wrong major (or "command not found"), `pnpm` is missing, a script that worked an hour ago now reports `Cannot find module`. Common in fresh shells, in CI step shells, and in non-interactive bash where nvm's auto-load did not run.

Cause: nvm only sources its initializer when the shell's startup files (`.bashrc`, `.zshrc`) explicitly do so. Non-interactive bash (the kind that runs scripts, hooks, and CI steps) does not load `.bashrc` by default, so nvm is invisible.

Fix:

1. Source nvm explicitly at the top of any non-interactive script that needs Node:
   ```bash
   export NVM_DIR="$HOME/.nvm"
   [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
   nvm use --silent  # picks up .nvmrc if present
   ```
2. Verify:
   ```bash
   node --version
   pnpm --version
   ```
3. If pnpm is still not installed at this Node version, install it pinned: `npm install -g pnpm@11.0.8` (the version pinned in `CLAUDE.md` "Tooling Map").

Avoid: do not put nvm-loading inside a function whose body runs in a subshell; nvm modifies the parent shell's PATH and `node` binary lookup. If you see odd "node: command not found" right after a successful nvm command, you are in a subshell.

### 2. Python `uv install` failures

Symptom: `uv sync` fails with a build error, with "no matching distribution found", or hangs at "resolving dependencies".

Fix sequence:

1. **Confirm the uv binary is current.** `uv --version`. If older than the version pinned in scripts, reinstall:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
   uv installs to `~/.local/bin/uv` (per `CLAUDE.md` "Tooling Map").
2. **Clear the cache.** A corrupted cache mimics a wide range of resolution errors:
   ```bash
   uv cache clean
   ```
3. **Confirm Python interpreter.** `uv` will fetch a Python if `pyproject.toml` requests one not installed; `uv python list` shows what it sees.
4. **Native build deps.** Wheels that build C extensions need a toolchain. On Debian/Ubuntu (WSL2 default):
   ```bash
   sudo apt-get install -y build-essential python3-dev libffi-dev libssl-dev
   ```
5. **Network.** Corporate VPNs / WSL2 mirror issues sometimes block PyPI. Test: `curl -I https://pypi.org/simple/`. If that fails, the issue is the network, not uv.
6. **Last resort.** Delete `.venv` and `uv.lock`, then `uv sync`. The lockfile rebuild is rarely necessary; do not check in a regenerated `uv.lock` without confirming the only diff is the failing package.

### 3. Pre-commit hooks blocking a commit

Symptom: `git commit` exits with one or more hook failures (gitleaks, trailing whitespace, em-dash check, terraform fmt, actionlint, etc.).

Diagnose:

1. Read the failure output. The hooks are configured in `.pre-commit-config.yaml`; each prints its own message.
2. Run the failing hook against the staged set to reproduce without committing:
   ```bash
   pre-commit run <hook-id> --all-files
   # or for just the staged files:
   pre-commit run <hook-id>
   ```
3. Common failures and fixes:
   - **`gitleaks`:** a real secret was caught. Do NOT just bypass. Rotate the credential, remove from history if needed (use `git filter-repo` not `git filter-branch`), and add an allowlist regex to `.gitleaks.toml` ONLY if it is a documented placeholder, not a real secret. See section 6 below for `.gitleaks.toml` extension.
   - **`detect-em-dashes`:** the script `scripts/check_no_em_dashes.sh` reports a file:line. Open the file, replace the em-dash (U+2014) or en-dash (U+2013) with a comma, period, parenthesis, semicolon, or hyphen. This is a hard rule; there is no allowlist.
   - **`trailing-whitespace` / `end-of-file-fixer`:** the hooks auto-fix; just `git add -u` and re-commit.
   - **`terraform_fmt`:** run `terraform fmt -recursive` from `infra/` and re-commit.
   - **`actionlint`:** the workflow YAML has a syntax or semantic error. Read the line:column from the output and fix.

Skipping with discipline:

- The skip mechanism is `SKIP=<hook-id> git commit ...` (e.g., `SKIP=gitleaks git commit -m "..."`). Use only for genuinely-broken-hook-not-broken-content cases (rare).
- A skipped hook is a deferred check, not a passed one. The server-side equivalents (gitleaks workflow, actionlint, etc.) catch what the local hook missed; expect the PR to fail CI if you skipped a real failure.
- Never use `--no-verify` to skip ALL hooks. That is the lazy version of `SKIP` and bypasses checks you did not intend to bypass.

### 4. pnpm-workspace.yaml `allowedBuilds` and `pnpm approve-builds --all`

Symptom: `pnpm install` reports "Ignored build scripts: <package>" for a transitive dep, or refuses to run the package's postinstall steps. As of pnpm 10+, build scripts must be explicitly approved per-package; the `package.json` `pnpm.onlyBuiltDependencies` allowlist moved to `pnpm-workspace.yaml`.

Fix:

1. The repo's `pnpm-workspace.yaml` carries the allowlist:
   ```yaml
   onlyBuiltDependencies:
     - <package-1>
     - <package-2>
   ```
2. After adding a new dependency that needs a build script (better-auth's bcrypt, biome's native binaries, etc.), run:
   ```bash
   pnpm install
   pnpm approve-builds --all
   ```
   `approve-builds` adds the relevant entries; commit `pnpm-workspace.yaml`.
3. If a single dep is the issue, scope tighter: `pnpm approve-builds <package-name>`.
4. Verify by deleting `node_modules/` and running `pnpm install` clean; postinstall scripts should run without complaint.

This was a one-time foot-gun on this project (see `feedback_panakoes_lessons` memory). Do not put the allowlist back into `package.json`; pnpm 10+ ignores it there.

### 5. Testcontainers + Docker socket on WSL2

Symptom: `pytest` fails with "Cannot connect to Docker daemon", "permission denied on /var/run/docker.sock", or testcontainers hangs at "starting container".

Cause: Docker on WSL2 runs via Docker Desktop's WSL integration. The socket is at `/var/run/docker.sock` and is owned by the `docker` group. WSL2's user is not in the `docker` group by default in some setups, and a fresh WSL distro install does not always have Docker integration enabled.

Fix:

1. **Confirm Docker Desktop's WSL integration is on.** Open Docker Desktop, Settings, Resources, WSL Integration; toggle on for the relevant distro; Apply & Restart. Re-check from the WSL shell:
   ```bash
   docker ps
   ```
2. **Group membership.** If `docker ps` reports a permission error:
   ```bash
   sudo groupadd -f docker
   sudo usermod -aG docker "$USER"
   newgrp docker
   docker ps  # should now work
   ```
3. **Testcontainers env override** (if the socket path is non-standard or you are using a remote Docker host):
   ```bash
   export DOCKER_HOST=unix:///var/run/docker.sock
   # or, with a remote daemon:
   export DOCKER_HOST=tcp://<host>:2375
   ```
4. **Slow first pull.** First-time tests pulling Postgres or Redis images can take 30-90 seconds; subsequent runs hit the cache. If a test reports "container start timed out" on cold cache, raise the testcontainers `wait_for` timeout in the test fixture, do not just rerun.

### 6. gitleaks false positives

Symptom: `gitleaks` blocks a commit on a string that is genuinely not a secret (a placeholder in docs, an example AWS key from upstream documentation, a regex pattern that happens to match).

Fix:

1. **Confirm it is genuinely not a secret** (do not allowlist real credentials).
2. **Extend `.gitleaks.toml`.** The file inherits the upstream default rule set via `useDefault = true`. Add a regex to the `[allowlist]` `regexes = [...]` array:
   ```toml
   regexes = [
       '''EXAMPLE_[A-Z_]+''',
       '''AKIAIOSFODNN7EXAMPLE''',
       '''wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY''',
       '''<your new pattern>''',
   ]
   ```
   Or scope by path under `paths = [...]` if the entire file is known-safe.
3. **Test before committing:**
   ```bash
   gitleaks protect --staged --verbose
   ```
4. **Document why** in a commit message or in a comment in `.gitleaks.toml`. A future reader needs to know "this regex is intentional" without having to chase the original PR.

Bypassing without extending the config (`SKIP=gitleaks git commit ...`) is a last resort and only acceptable for one-off commits where the surrounding context already made the false-positive obvious.

### 7. Terraform state lock conflicts

Symptom: `terraform plan` or `terraform apply` hangs at "Acquiring state lock", then fails with `Error: Error acquiring the state lock`, often referencing a `LockID` and the holder.

Cause: another Terraform process holds the DynamoDB lock, OR a prior process died without releasing the lock. Concurrent agent runs make this common; the fix is patience plus a longer timeout.

Fix:

1. **First, try `-lock-timeout=2m`** on the failing command:
   ```bash
   terraform plan -lock-timeout=2m
   terraform apply -lock-timeout=2m
   ```
   This makes Terraform poll for up to 2 minutes before giving up. In parallel-agent setups (per `feedback_panakoes_lessons` memory), this is enough to ride out short overlaps.
2. **If the lock persists past 2 minutes,** confirm no other Terraform run is in progress (check `.agent-runs/` for active sub-agents, check shells, check CI). If certain, force-unlock with the lock ID from the error message:
   ```bash
   terraform force-unlock <lock-id>
   ```
   Force-unlocking while another process holds it produces state corruption; do this only after positive confirmation.
3. **Consider whether the lock pattern indicates a deeper problem.** If you regularly hit lock conflicts, refactor: split the configuration into smaller modules with separate state files, or serialize the agent runs that touch the same module.

### 8. `.gitattributes` `merge=union` for CHANGELOG.md

Symptom: rebasing or merging two feature branches produces conflict markers in `CHANGELOG.md` even though both sides only added new entries to `[Unreleased]`.

Cause: this should not happen on `main`. Per ADR-026 (`docs/adr/ADR-026-changelog-merge-union.md`), `.gitattributes` declares:
```
CHANGELOG.md merge=union
```
which makes git union both sides automatically with no conflict markers.

If it IS happening:

1. **Confirm `.gitattributes` exists at the repo root and contains the line:**
   ```bash
   cat .gitattributes
   # expected line: CHANGELOG.md merge=union
   ```
2. **Confirm the merge is actually using gitattributes.** Some tools (especially IDE merge UIs) bypass gitattributes. From the CLI:
   ```bash
   git rebase main
   # or
   git merge main
   ```
3. **If you are using a worktree,** the `.gitattributes` lives in the parent repo and applies to the worktree automatically. Confirm with `git check-attr merge CHANGELOG.md`; expected output ends with `merge: union`.
4. **Do not extend `merge=union` to other files.** Per ADR-026's "Consequences", the strategy is correct ONLY for append-only documents like CHANGELOG. Source code with `merge=union` would silently corrupt at merge time.
5. If you genuinely need a similar pattern for another append-only file (a new release-notes file, a contributors list), open a separate ADR following the template in `docs/adr/README.md`.

## Verification

For each section's fix, the verification is the same: re-run the originally-failing command and confirm it now succeeds. For `pre-commit` issues, run `pre-commit run --all-files` to confirm a clean pass. For Terraform issues, run `terraform plan` and confirm it returns "No changes" (assuming no infra change was intended).

## Rollback

Most steps in this runbook are local-machine fixes; rollback means undoing the local change (revert the cache delete, drop the user from the docker group, remove the gitleaks allowlist regex, etc.). Repo-side changes (extending `.gitleaks.toml`, adding to `pnpm-workspace.yaml`'s `onlyBuiltDependencies`) are revertable via `git revert` once committed.

## References

- `CLAUDE.md` "Tooling Map" for pinned versions of pnpm, biome, uv, terraform, etc.
- `CLAUDE.md` "Discipline Rules" for the no-em-dash rule and the Conventional Commits + CHANGELOG discipline.
- `.pre-commit-config.yaml` for the canonical hook list and pinned versions.
- `.gitleaks.toml` for the active allowlist.
- `scripts/check_no_em_dashes.sh` for the em-dash hook implementation.
- `docs/adr/ADR-026-changelog-merge-union.md` for the `merge=union` strategy and its scope rules.
- `feedback_panakoes_lessons` memory for the original incidents that produced the nvm-load, pnpm-allowedBuilds, terraform-lock-timeout, and Dependabot-secrets lessons.
- `incident-response.md` if a local dev signal turns out to indicate a production issue.
