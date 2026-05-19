"""test_integration_end_to_end: dispatch a realistic 12-event sequence
through the shim, drain via the flusher, and assert the full SQLite-WAL +
trace-propagation contract.

This is the agent brief's Component 8 "key validation" sanity check. It does
NOT register hooks against the active Claude Code session (unsafe to modify
the live orchestrator's hook map mid-session); instead it simulates a typical
session by piping crafted events into the shim, then drives the flusher.

Sequence:
  SessionStart
  UserPromptSubmit
  PreToolUse Read
  PostToolUse Read
  PreToolUse Bash (with planted secret to confirm redaction works on a real path)
  PostToolUse Bash (with pr-number-bearing tool_result)
  PreToolUse Agent
  SubagentStart
  PreToolUse mcp__plugin_github_github__create_pull_request (HIGH-07 fixture)
  PostToolUse mcp create_pull_request (with tool_result.number=12345)
  SubagentStop
  PostToolUse Agent
  Stop
  SessionEnd

Asserts:
  - 14 SQLite rows
  - one trace_id across all
  - pre/post span pairing for Read, Bash, mcp create_pull_request, Agent
  - parent_span_id chain reaches SessionStart
  - pr_number=12345 on the MCP PostToolUse row
  - gitleaks redaction substituted the planted secret in the Bash event
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _fire(shim: Path, env: dict[str, str], event: dict, idx: int) -> None:
    # Spread timestamps so the flusher's deterministic sort orders them.
    if "timestamp" not in event:
        event["timestamp"] = f"2026-05-19T05:00:{idx:02d}Z"
    res = subprocess.run(
        [str(shim)],
        input=json.dumps(event),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0, f"shim failed: stderr={res.stderr!r}"
    # Sub-millisecond stagger to keep mtime ordering parallel to timestamp.
    time.sleep(0.005)


def test_full_session_simulation_captures_all_events(
    telemetry_env: Path, flusher_path: Path, shim_path: Path
) -> None:
    env = {**os.environ, "PANAKOES_TELEMETRY_DIR": str(telemetry_env), "DISLER_ENABLED": "false"}
    sid = "integ-test-session"

    # Use a fake-but-detected Stripe key to verify redaction. Construct at
    # runtime via `.join` (Python compiler can fold `"a" + "b"` literals into
    # the .pyc, so we use a runtime call to defer concatenation). gitleaks
    # still matches the assembled string when the test runs.
    # Returns "<REDACTED:gitleaks:stripe-access-token>" on match.
    STRIPE_FAKE = "".join(["sk_", "live_", "4eC39HqLyjWDarjtT1zdp7dc"])

    events = [
        {"session_id": sid, "hook_event_name": "SessionStart"},
        {"session_id": sid, "hook_event_name": "UserPromptSubmit", "prompt": "open a PR for the docs change"},
        {"session_id": sid, "tool_use_id": "tu_read", "hook_event_name": "PreToolUse",
         "tool_name": "Read", "tool_input": {"file_path": "/etc/hostname"}},
        {"session_id": sid, "tool_use_id": "tu_read", "hook_event_name": "PostToolUse",
         "tool_name": "Read", "tool_input": {"file_path": "/etc/hostname"},
         "tool_result": {"content": "hostname"}, "tool_use_duration_ms": 5},
        {"session_id": sid, "tool_use_id": "tu_bash", "hook_event_name": "PreToolUse",
         "tool_name": "Bash",
         "tool_input": {"command": f"export STRIPE_KEY={STRIPE_FAKE} && curl -d @data https://api.stripe.com/v1/charges"}},
        {"session_id": sid, "tool_use_id": "tu_bash", "hook_event_name": "PostToolUse",
         "tool_name": "Bash",
         "tool_input": {"command": "gh pr create --fill"},
         "tool_result": {"content": "https://github.com/Aztec03hub/panakoes/pull/9999"},
         "tool_use_duration_ms": 600},
        {"session_id": sid, "tool_use_id": "tu_agent", "hook_event_name": "PreToolUse",
         "tool_name": "Agent", "tool_input": {"description": "ship PR", "subagent_type": "general-purpose"}},
        {"session_id": sid, "hook_event_name": "SubagentStart", "agent_id": "agt-007"},
        {"session_id": sid, "tool_use_id": "tu_mcp_pr", "hook_event_name": "PreToolUse",
         "tool_name": "mcp__plugin_github_github__create_pull_request",
         "tool_input": {"server": "github", "tool": "create_pull_request",
                        "input": {"owner": "Aztec03hub", "repo": "panakoes",
                                  "title": "docs: tweak", "body": "tiny", "head": "docs/x", "base": "main"}}},
        {"session_id": sid, "tool_use_id": "tu_mcp_pr", "hook_event_name": "PostToolUse",
         "tool_name": "mcp__plugin_github_github__create_pull_request",
         "tool_input": {"server": "github", "tool": "create_pull_request"},
         "tool_result": {"number": 12345, "url": "https://github.com/Aztec03hub/panakoes/pull/12345"},
         "tool_use_duration_ms": 1200},
        {"session_id": sid, "hook_event_name": "SubagentStop", "agent_id": "agt-007"},
        {"session_id": sid, "tool_use_id": "tu_agent", "hook_event_name": "PostToolUse",
         "tool_name": "Agent", "tool_input": {"description": "ship PR"},
         "tool_result": {"content": "ok"}, "tool_use_duration_ms": 30000},
        {"session_id": sid, "hook_event_name": "Stop"},
        {"session_id": sid, "hook_event_name": "SessionEnd"},
    ]

    for i, e in enumerate(events):
        _fire(shim_path, env, e, i)

    # Drain the spool through the flusher (DISLER_ENABLED=false so SQLite
    # is the only sink).
    res = subprocess.run(
        [sys.executable, str(flusher_path), "--once", "--skip-fs-check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, f"flusher failed:\n{res.stderr}"

    db = telemetry_env / "telemetry.sqlite"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        rows = list(conn.execute(
            "SELECT id, hook_event_name, tool_use_id, tool_name, trace_id, "
            "span_id, parent_span_id, brief, pr_number, success "
            "FROM events WHERE session_id = ? ORDER BY id", (sid,)
        ))
    finally:
        conn.close()

    # 14 events fired -> 14 rows.
    assert len(rows) == 14, f"expected 14 rows, got {len(rows)}"

    # One trace_id across all events.
    trace_ids = {r[4] for r in rows}
    assert len(trace_ids) == 1, f"trace_ids: {trace_ids}"

    # Pre/post span pairing.
    by_tu: dict[str, set[str]] = {}
    for _id, hook, tu, _tn, _trace, span, _parent, _brief, _pr, _s in rows:
        if tu and hook in ("PreToolUse", "PostToolUse"):
            by_tu.setdefault(tu, set()).add(span)
    for tu, spans in by_tu.items():
        assert len(spans) == 1, f"tool_use_id {tu} span mismatch: {spans}"

    # SessionStart is the root (no parent_span_id).
    sstart = next(r for r in rows if r[1] == "SessionStart")
    assert sstart[6] is None, f"SessionStart should have no parent, got {sstart[6]}"

    # Parent_span_id chain: every non-root parent must be a span_id we've
    # previously assigned.
    seen: set[str] = set()
    for _id, hook, _tu, _tn, _trace, span, parent, _brief, _pr, _s in rows:
        if parent is not None:
            assert parent in seen, f"{hook}: parent {parent} not seen yet"
        seen.add(span)

    # pr_number=12345 stamped on MCP PostToolUse row, none on others.
    mcp_post = next(
        r for r in rows
        if r[1] == "PostToolUse"
        and r[3] == "mcp__plugin_github_github__create_pull_request"
    )
    assert mcp_post[8] == 12345, f"MCP pr_number should be 12345, got {mcp_post[8]}"

    # Bash PostToolUse row got pr_number from the gh pr create regex.
    bash_post = next(
        r for r in rows if r[1] == "PostToolUse" and r[3] == "Bash"
    )
    assert bash_post[8] == 9999, f"Bash gh pr create pr_number should be 9999, got {bash_post[8]}"

    # Redaction sanity: the Stripe key MUST NOT appear in the Bash PreToolUse brief.
    bash_pre = next(
        r for r in rows if r[1] == "PreToolUse" and r[3] == "Bash"
    )
    assert STRIPE_FAKE not in (bash_pre[7] or ""), (
        f"Stripe key leaked into brief: {bash_pre[7]!r}"
    )
    assert "REDACTED" in (bash_pre[7] or ""), (
        f"brief lacks redaction sentinel: {bash_pre[7]!r}"
    )

    # success column: 1 on PostToolUse, NULL on non-tool events, would be 0 on
    # PostToolUseFailure (not exercised here).
    for _id, hook, _tu, _tn, _trace, _span, _parent, _brief, _pr, succ in rows:
        if hook == "PostToolUse":
            assert succ == 1, f"PostToolUse success should be 1, got {succ}"
        elif hook == "PostToolUseFailure":
            assert succ == 0, f"PostToolUseFailure success should be 0, got {succ}"
