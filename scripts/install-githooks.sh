#!/usr/bin/env bash
# install-githooks.sh: point this clone's git hooks at the version-controlled
# .githooks/ directory.
#
# Idempotent. Run once per clone. The setting persists in .git/config.
# Hooks under .githooks/ are then resolved automatically; new hooks added
# to the directory in future commits work without re-running this.
#
# Install: scripts/install-githooks.sh  OR  make install-hooks

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  echo "install-githooks: not inside a git repo" >&2
  exit 1
fi

cd "$REPO_ROOT"

if [ ! -d .githooks ]; then
  echo "install-githooks: .githooks/ directory missing at $REPO_ROOT" >&2
  exit 1
fi

# Make every hook under .githooks/ executable. .git considers the file's
# x-bit when running it.
chmod +x .githooks/* 2>/dev/null || true

current=$(git config --get core.hooksPath 2>/dev/null || true)
if [ "$current" = ".githooks" ]; then
  echo "install-githooks: already configured (core.hooksPath=.githooks)."
else
  git config core.hooksPath .githooks
  echo "install-githooks: set core.hooksPath=.githooks (was: ${current:-default})."
fi

echo ""
echo "Active hooks:"
ls -1 .githooks/ | sed 's/^/  /'
