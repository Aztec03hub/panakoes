"""test_trace_propagation: prove the flusher's W3C Trace Context propagation
satisfies design Section 3.5 step 3-4 (CRIT-01).

Synthesizes an event sequence directly into the spool:

  SessionStart
  PreToolUse toolu_A
  PostToolUse toolu_A
  PreToolUse toolu_B
  PostToolUse toolu_B
  SubagentStart (different session)
  PreToolUse toolu_C inside subagent
  PostToolUse toolu_C
  SubagentStop
  SessionEnd

Then runs the flusher's `--once` mode and asserts:
  (a) every event shares the same trace_id (within a session)
  (b) PreToolUse and PostToolUse for the same tool_use_id share span_id
  (c) parent_span_id chain forms a valid tree (every non-root parent must be a
      previously-seen span_id)
  (d) SubagentStart's children have parent_span_id pointing at SubagentStart's
      span_id (when in the same session)
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


def _drop_event(spool: Path, sid: str, event: dict) -> None:
    sess_dir = spool / "spool" / sid
    sess_dir.mkdir(parents=True, exist_ok=True)
    name = f"{event.get('tool_use_id', event['hook_event_name'])}-{event['hook_event_name']}-{time.time_ns()}-{os.getpid()}.json"
    (sess_dir / name).write_text(json.dumps(event), encoding="utf-8")


def _run_flusher_once(state_dir: Path, flusher: Path) -> int:
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
    return res.returncode


def test_trace_id_shared_per_session_and_span_pairs_match(
    telemetry_env: Path, flusher_path: Path
) -> None:
    sid = "trace-test-A"
    events = [
        {"session_id": sid, "hook_event_name": "SessionStart", "timestamp": "2026-05-19T04:30:00Z"},
        {
            "session_id": sid,
            "tool_use_id": "toolu_A",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls /"},
            "timestamp": "2026-05-19T04:30:01Z",
        },
        {
            "session_id": sid,
            "tool_use_id": "toolu_A",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls /"},
            "tool_result": {"content": "etc\nroot"},
            "tool_use_duration_ms": 8,
            "timestamp": "2026-05-19T04:30:02Z",
        },
        {
            "session_id": sid,
            "tool_use_id": "toolu_B",
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/etc/hostname"},
            "timestamp": "2026-05-19T04:30:03Z",
        },
        {
            "session_id": sid,
            "tool_use_id": "toolu_B",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_input": {"file_path": "/etc/hostname"},
            "tool_result": {"content": "hostname"},
            "tool_use_duration_ms": 4,
            "timestamp": "2026-05-19T04:30:04Z",
        },
        {"session_id": sid, "hook_event_name": "SessionEnd", "timestamp": "2026-05-19T04:30:05Z"},
    ]
    for e in events:
        _drop_event(telemetry_env, sid, e)

    rc = _run_flusher_once(telemetry_env, flusher_path)
    assert rc == 0, "flusher --once must exit 0"

    db = telemetry_env / "telemetry.sqlite"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        rows = list(
            conn.execute(
                "SELECT hook_event_name, tool_use_id, trace_id, span_id, parent_span_id "
                "FROM events WHERE session_id = ? ORDER BY id",
                (sid,),
            )
        )
    finally:
        conn.close()

    assert len(rows) == 6, f"expected 6 rows, got {len(rows)}"

    # (a) every event shares the same trace_id within the session
    trace_ids = {r[2] for r in rows}
    assert len(trace_ids) == 1, f"trace_ids should be unique per session, got {trace_ids}"

    # (b) PreToolUse and PostToolUse for the same tool_use_id share span_id
    by_tuid: dict[str, list[str]] = {}
    for hook, tuid, _trace, span, _parent in rows:
        if tuid:
            by_tuid.setdefault(tuid, []).append(span)
    for tuid, spans in by_tuid.items():
        assert len(set(spans)) == 1, (
            f"tool_use_id {tuid}: pre/post must share span_id, got {spans}"
        )

    # (c) parent_span_id chain: every non-root parent must be a previously-seen span_id
    seen_spans: set[str] = set()
    for hook, _tuid, _trace, span, parent in rows:
        if parent is not None:
            assert parent in seen_spans, (
                f"parent_span_id {parent} for {hook} not in previously-seen spans"
            )
        # SessionStart is the root (no parent).
        if hook == "SessionStart":
            assert parent is None, "SessionStart should have no parent"
        seen_spans.add(span)


def test_subagent_dispatch_chains_correctly(
    telemetry_env: Path, flusher_path: Path
) -> None:
    """SubagentStart's children must have parent_span_id pointing to it."""
    sid = "trace-test-B"
    # Explicit ISO 8601 timestamps so the flusher's spool ordering is
    # deterministic (ext4 mtime resolution can coalesce sub-millisecond writes).
    base = "2026-05-19T04:30:0"
    events = [
        {"session_id": sid, "hook_event_name": "SessionStart", "timestamp": base + "0Z"},
        {
            "session_id": sid,
            "tool_use_id": "toolu_agent",
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {"description": "do thing", "subagent_type": "general-purpose"},
            "timestamp": base + "1Z",
        },
        {"session_id": sid, "hook_event_name": "SubagentStart", "agent_id": "agt-001", "timestamp": base + "2Z"},
        {
            "session_id": sid,
            "tool_use_id": "toolu_inside",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "uname"},
            "agent_id": "agt-001",
            "timestamp": base + "3Z",
        },
        {
            "session_id": sid,
            "tool_use_id": "toolu_inside",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "uname"},
            "tool_result": {"content": "Linux"},
            "agent_id": "agt-001",
            "timestamp": base + "4Z",
        },
        {"session_id": sid, "hook_event_name": "SubagentStop", "agent_id": "agt-001", "timestamp": base + "5Z"},
        {
            "session_id": sid,
            "tool_use_id": "toolu_agent",
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "tool_input": {"description": "do thing"},
            "tool_result": {"content": "ok"},
            "timestamp": base + "6Z",
        },
        {"session_id": sid, "hook_event_name": "SessionEnd", "timestamp": base + "7Z"},
    ]
    for e in events:
        _drop_event(telemetry_env, sid, e)
    rc = _run_flusher_once(telemetry_env, flusher_path)
    assert rc == 0

    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        rows = list(
            conn.execute(
                "SELECT hook_event_name, tool_use_id, trace_id, span_id, parent_span_id "
                "FROM events WHERE session_id = ? ORDER BY id",
                (sid,),
            )
        )
    finally:
        conn.close()

    # All same trace_id
    trace_ids = {r[2] for r in rows}
    assert len(trace_ids) == 1, f"single trace_id per session, got {trace_ids}"

    # The Bash tool call inside the subagent must have parent_span_id pointing
    # at the SubagentStart's span_id.
    subagent_start = next(r for r in rows if r[0] == "SubagentStart")
    sa_span = subagent_start[3]
    inside_pre = next(
        r for r in rows if r[0] == "PreToolUse" and r[1] == "toolu_inside"
    )
    assert inside_pre[4] == sa_span, (
        f"PreToolUse inside subagent should have parent_span_id={sa_span}, "
        f"got {inside_pre[4]}"
    )
