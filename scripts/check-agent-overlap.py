#!/usr/bin/env python3
"""Detect file-set overlap across sub-agent briefs before dispatch.

Each brief is a Markdown file containing an `EXPECTED FILES MODIFIED` section.
Lines under that section (until the next ALL-CAPS header or EOF) are treated as
file paths or shell-style globs. The script computes the pairwise intersection
across briefs and reports any pairs whose declared file sets overlap, so the
orchestrator can merge the briefs into a single batched PR rather than dispatch
parallel sub-agents that would later cascade-rebase on the same files.

Why this exists: see CLAUDE.md "PR batching to reduce conflict surface" and
~/.claude/.../memory/feedback_pr_batching_to_reduce_conflict_surface.md.

Usage:
    scripts/check-agent-overlap.py BRIEF [BRIEF ...]
    scripts/check-agent-overlap.py --dir briefs/
    scripts/check-agent-overlap.py --json BRIEF [BRIEF ...]

Exit codes:
    0 = no overlap detected
    1 = at least one pair of briefs declared overlapping files
    2 = usage error (no briefs found, unreadable input, etc.)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from itertools import combinations
from pathlib import Path

SECTION_RE = re.compile(r"^\s*#{0,6}\s*EXPECTED FILES MODIFIED\b", re.IGNORECASE)
NEXT_HEADER_RE = re.compile(r"^\s*#{1,6}\s+\S|^[A-Z][A-Z0-9 _\-]{3,}:\s*$")


def extract_files(brief_path: Path) -> set[str]:
    """Return the declared file set from a brief's EXPECTED FILES MODIFIED block."""
    text = brief_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    files: set[str] = set()
    in_section = False
    for line in lines:
        if SECTION_RE.match(line):
            in_section = True
            continue
        if in_section:
            if line.strip() and NEXT_HEADER_RE.match(line) and not SECTION_RE.match(line):
                break
            entry = line.strip().lstrip("-*").strip().strip("`").strip()
            if not entry or entry.startswith("("):
                continue
            files.add(entry)
    return files


def files_overlap(a: set[str], b: set[str]) -> set[str]:
    """Return the overlap between two file/glob sets (glob-aware)."""
    overlap: set[str] = set()
    for pa in a:
        for pb in b:
            if pa == pb or fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa):
                overlap.add(pa)
                overlap.add(pb)
    return overlap


def collect_briefs(args: argparse.Namespace) -> list[Path]:
    briefs: list[Path] = []
    if args.dir:
        d = Path(args.dir)
        if not d.is_dir():
            print(f"error: --dir {args.dir} is not a directory", file=sys.stderr)
            sys.exit(2)
        briefs.extend(sorted(d.glob("*.md")))
    briefs.extend(Path(b) for b in args.briefs)
    return [b for b in briefs if b.is_file()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("briefs", nargs="*", help="Brief Markdown files")
    parser.add_argument("--dir", help="Directory of brief Markdown files")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args(argv)

    briefs = collect_briefs(args)
    if not briefs:
        print("error: no briefs provided", file=sys.stderr)
        return 2

    parsed = {b: extract_files(b) for b in briefs}
    conflicts = []
    for a, b in combinations(briefs, 2):
        ov = files_overlap(parsed[a], parsed[b])
        if ov:
            conflicts.append({"a": str(a), "b": str(b), "overlap": sorted(ov)})

    if args.json:
        print(json.dumps({"briefs": [str(b) for b in briefs], "conflicts": conflicts}, indent=2))
    else:
        if not conflicts:
            print(f"OK: no overlap across {len(briefs)} brief(s)")
        else:
            print(f"OVERLAP DETECTED across {len(conflicts)} brief pair(s):")
            for c in conflicts:
                print(f"  {c['a']} <-> {c['b']}")
                for f in c["overlap"]:
                    print(f"    {f}")
            print("\nMerge these briefs into a single batched PR per CLAUDE.md 'PR batching to reduce conflict surface'.")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
