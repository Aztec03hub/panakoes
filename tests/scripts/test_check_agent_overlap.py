"""Tests for scripts/check-agent-overlap.py.

Covers the brief parser (EXPECTED FILES MODIFIED block extraction), pairwise
overlap detection (exact match + glob match), exit codes, and the CLI shape.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check-agent-overlap.py"

_spec = importlib.util.spec_from_file_location("check_agent_overlap", SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def write_brief(tmp_path: Path, name: str, files: list[str], trailing: str = "") -> Path:
    body = (
        f"# Brief {name}\n\n"
        "TASK: do a thing.\n\n"
        "EXPECTED FILES MODIFIED (declare upfront so the orchestrator can detect overlap)\n"
    )
    body += "\n".join(f"- {f}" for f in files)
    body += "\n\nDISCIPLINE:\n- Conventional Commits.\n"
    body += trailing
    p = tmp_path / f"{name}.md"
    p.write_text(body)
    return p


def test_extract_files_basic(tmp_path):
    p = write_brief(tmp_path, "a", ["infra/dev/ecs/variables.tf", "CHANGELOG.md"])
    assert mod.extract_files(p) == {"infra/dev/ecs/variables.tf", "CHANGELOG.md"}


def test_extract_files_stops_at_next_header(tmp_path):
    p = write_brief(tmp_path, "a", ["infra/dev/ecs/variables.tf"], trailing="\nSCOPE:\n- only here\n")
    files = mod.extract_files(p)
    assert files == {"infra/dev/ecs/variables.tf"}
    assert "only here" not in files


def test_no_overlap(tmp_path):
    a = write_brief(tmp_path, "a", ["services/auth/src/index.ts"])
    b = write_brief(tmp_path, "b", ["services/billing/src/index.ts"])
    rc = mod.main([str(a), str(b)])
    assert rc == 0


def test_exact_overlap(tmp_path):
    a = write_brief(tmp_path, "a", ["infra/dev/ecs/variables.tf", "CHANGELOG.md"])
    b = write_brief(tmp_path, "b", ["infra/dev/ecs/variables.tf"])
    rc = mod.main([str(a), str(b)])
    assert rc == 1


def test_glob_overlap(tmp_path):
    a = write_brief(tmp_path, "a", ["infra/dev/ecs/*.tf"])
    b = write_brief(tmp_path, "b", ["infra/dev/ecs/variables.tf"])
    rc = mod.main([str(a), str(b)])
    assert rc == 1


def test_json_output(tmp_path, capsys):
    a = write_brief(tmp_path, "a", ["infra/dev/ecs/variables.tf"])
    b = write_brief(tmp_path, "b", ["infra/dev/ecs/variables.tf"])
    rc = mod.main([str(a), str(b), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 1
    assert len(payload["conflicts"]) == 1
    assert payload["conflicts"][0]["overlap"] == ["infra/dev/ecs/variables.tf"]


def test_dir_mode(tmp_path):
    write_brief(tmp_path, "a", ["services/auth/src/index.ts"])
    write_brief(tmp_path, "b", ["services/billing/src/index.ts"])
    rc = mod.main(["--dir", str(tmp_path)])
    assert rc == 0


def test_no_briefs_is_usage_error(tmp_path):
    rc = mod.main([])
    assert rc == 2


def test_cli_invocation(tmp_path):
    a = write_brief(tmp_path, "a", ["x.tf"])
    b = write_brief(tmp_path, "b", ["x.tf"])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(a), str(b)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "OVERLAP DETECTED" in proc.stdout
