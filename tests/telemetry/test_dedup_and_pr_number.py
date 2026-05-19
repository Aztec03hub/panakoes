"""test_dedup_and_pr_number: confirm INSERT OR IGNORE + dedup_key (MED-02) and
pr_number extraction from `gh pr create` Bash output and the MCP
create_pull_request tool (CRIT-03).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _drop(spool: Path, sid: str, event: dict, suffix: str = "") -> None:
    sess = spool / "spool" / sid
    sess.mkdir(parents=True, exist_ok=True)
    import time as _t

    name = f"{event.get('tool_use_id') or event['hook_event_name']}-{event['hook_event_name']}-{_t.time_ns()}{suffix}.json"
    (sess / name).write_text(json.dumps(event), encoding="utf-8")


def _drain(state_dir: Path, flusher: Path) -> None:
    env = {
        **os.environ,
        "PANAKOES_TELEMETRY_DIR": str(state_dir),
        "DISLER_ENABLED": "false",
    }
    res = subprocess.run(
        [sys.executable, str(flusher), "--once", "--skip-fs-check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr


def test_duplicate_event_files_produce_one_row(
    telemetry_env: Path, flusher_path: Path
) -> None:
    sid = "dedup-test"
    base = {
        "session_id": sid,
        "tool_use_id": "toolu_dup",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
    }
    # Drop the same event twice with different filenames (simulates a crash-replay).
    _drop(telemetry_env, sid, base, suffix="-a")
    _drop(telemetry_env, sid, base, suffix="-b")
    _drain(telemetry_env, flusher_path)
    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ? AND tool_use_id = ?",
            (sid, "toolu_dup"),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1, f"expected 1 row after dedup, got {n}"


def test_pr_number_extracted_from_gh_pr_create_bash_output(
    telemetry_env: Path, flusher_path: Path
) -> None:
    sid = "pr-bash-test"
    pre = {
        "session_id": sid,
        "tool_use_id": "toolu_pr",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --fill"},
    }
    post = {
        "session_id": sid,
        "tool_use_id": "toolu_pr",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr create --fill"},
        "tool_result": {
            "content": "Creating pull request for feat/x into main in Aztec03hub/panakoes\nhttps://github.com/Aztec03hub/panakoes/pull/12345"
        },
    }
    _drop(telemetry_env, sid, pre)
    _drop(telemetry_env, sid, post, suffix="-post")
    _drain(telemetry_env, flusher_path)
    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT pr_number FROM events WHERE tool_use_id = ? AND hook_event_name = 'PostToolUse'",
            ("toolu_pr",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 12345, f"expected pr_number=12345, got {row[0]}"


def test_pr_number_extracted_from_mcp_create_pull_request(
    telemetry_env: Path, flusher_path: Path
) -> None:
    sid = "pr-mcp-test"
    tool_name = "mcp__plugin_github_github__create_pull_request"
    post = {
        "session_id": sid,
        "tool_use_id": "toolu_mcp_pr",
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {
            "server": "github",
            "tool": "create_pull_request",
            "input": {"owner": "Aztec03hub", "repo": "panakoes", "title": "x", "body": "y", "head": "f", "base": "main"},
        },
        "tool_result": {"number": 67890, "url": "https://github.com/Aztec03hub/panakoes/pull/67890"},
    }
    _drop(telemetry_env, sid, post)
    _drain(telemetry_env, flusher_path)
    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT pr_number FROM events WHERE tool_use_id = ?", ("toolu_mcp_pr",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 67890, f"expected pr_number=67890, got {row[0]}"
