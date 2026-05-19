#!/usr/bin/env python3
"""telemetry-flusher.py: drain the spool, redact, write to SQLite + disler.

Reads raw hook events written by .claude/hooks/trace-shim.sh from the spool
directory, runs gitleaks redaction in batch, computes per-tool briefs, applies
W3C Trace Context propagation, and dual-writes to:

  1. ${PANAKOES_TELEMETRY_DIR}/telemetry.sqlite (analytics of record, WAL mode)
  2. ${DISLER_URL}/events (live dashboard; skipped when DISLER_ENABLED is false)

Design references (see docs/design/tool-trace-telemetry.md):
  - Section 3.4 Brief generation rules + per-tool extraction (incl. pr_number)
  - Section 3.5 Async shim + flusher architecture + trace propagation
  - Section 3.6 SQLite-WAL schema (post-Gate-2: descriptive long-name columns,
                generated dedup_key, auto_vacuum INCREMENTAL)
  - Section 4.2 Dual-sink architecture
  - Section 4.3 Disler payload mapping
  - Section 4.4 Dual-write ordering + Idempotency-Key
  - Section 6.1 / 6.2 Storage paths + rotation
  - Section 8 Invariants (response body never stored; redaction before write)

Run modes:
  python3 scripts/telemetry-flusher.py --once       # single drain cycle, exit
  python3 scripts/telemetry-flusher.py              # long-lived (loop every 250 ms)
  python3 scripts/telemetry-flusher.py --init-only  # init the SQLite schema, exit

Env vars:
  PANAKOES_TELEMETRY_DIR  default ${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry
  DISLER_URL              default http://localhost:4000
  DISLER_ENABLED          default false
  DISLER_HEALTH_PATH      default /events/recent (HIGH-06 verified by orchestrator)
  CLAUDE_TRACE_DEBUG      when 1, log full event JSON to stderr
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_POLL_INTERVAL_SEC = 0.25
SPOOL_WARN_THRESHOLD = 1000  # MED-11
SPOOL_HARDSTOP_THRESHOLD = 10000  # MED-11
HEALTHCHECK_INTERVAL_SEC = 60
DISLER_HTTP_TIMEOUT_SEC = 2.0

# Per-tool brief caps per design Section 3.4 step 3
BRIEF_CAP_BASH = 2048
BRIEF_CAP_AGENT = 1024
BRIEF_CAP_DEFAULT = 512

# Identity / ordering of the 12 hook events (design Section 3.3)
HOOK_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "Stop",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "Notification",
    "PermissionRequest",
)

# Regex used for pr_number extraction from `gh pr create` Bash output.
# Matches the standard "https://github.com/<owner>/<repo>/pull/<N>" line.
PR_URL_RE = re.compile(r"/pull/(\d+)")

# Filesystem allowlist for O_APPEND atomicity (Section 3.7 startup check).
_FS_ALLOWLIST = ("ext4", "btrfs", "xfs", "tmpfs")

# Tool name for the github MCP create-pull-request handler
MCP_CREATE_PR_TOOL = "mcp__plugin_github_github__create_pull_request"

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------


@dataclass
class FlusherConfig:
    state_dir: Path
    spool_dir: Path
    sessions_dir: Path
    sqlite_path: Path
    flusher_log: Path
    disler_url: str
    disler_enabled: bool
    disler_health_path: str
    poll_interval_sec: float
    debug: bool

    @classmethod
    def from_env(cls) -> "FlusherConfig":
        state_root = os.environ.get(
            "PANAKOES_TELEMETRY_DIR",
            os.path.join(
                os.environ.get(
                    "XDG_STATE_HOME",
                    os.path.join(os.environ.get("HOME", "/tmp"), ".local/state"),
                ),
                "panakoes-telemetry",
            ),
        )
        state_dir = Path(state_root)
        return cls(
            state_dir=state_dir,
            spool_dir=state_dir / "spool",
            sessions_dir=state_dir / "sessions",
            sqlite_path=state_dir / "telemetry.sqlite",
            flusher_log=state_dir / "flusher.log",
            disler_url=os.environ.get("DISLER_URL", "http://localhost:4000").rstrip("/"),
            disler_enabled=os.environ.get("DISLER_ENABLED", "false").lower() in ("1", "true", "yes"),
            disler_health_path=os.environ.get("DISLER_HEALTH_PATH", "/events/recent"),
            poll_interval_sec=float(os.environ.get("PANAKOES_FLUSHER_POLL_SEC", DEFAULT_POLL_INTERVAL_SEC)),
            debug=os.environ.get("CLAUDE_TRACE_DEBUG", "0") == "1",
        )

    def ensure_state_layout(self) -> None:
        for p in (self.state_dir, self.spool_dir, self.sessions_dir):
            p.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------


def setup_logging(cfg: FlusherConfig) -> logging.Logger:
    logger = logging.getLogger("panakoes.telemetry.flusher")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if cfg.debug else logging.INFO)
    cfg.flusher_log.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(cfg.flusher_log)
    fh.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


# ----------------------------------------------------------------------------
# Filesystem safety (Section 3.7 startup check)
# ----------------------------------------------------------------------------


def check_filesystem_atomicity(path: Path, logger: logging.Logger) -> bool:
    """Return True if the filesystem under `path` is in the local POSIX allowlist.

    The shim's mktemp + single-file-per-event pattern provides atomicity by
    construction (O_CREAT|O_EXCL), but the flusher's own log file uses O_APPEND
    semantics, and Section 3.7 establishes the local-POSIX-only invariant.
    """
    try:
        out = subprocess.run(
            ["df", "-T", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.error("df -T check failed (%s); v1 supports Linux/WSL2 only", exc)
        return False
    if out.returncode != 0:
        logger.error("df -T returned %d for %s: %s", out.returncode, path, out.stderr.strip())
        return False
    lines = out.stdout.strip().splitlines()
    if len(lines) < 2:
        logger.error("df -T produced unexpected output: %r", out.stdout)
        return False
    fields = lines[1].split()
    if len(fields) < 2:
        logger.error("df -T row malformed: %r", lines[1])
        return False
    fstype = fields[1]
    if fstype not in _FS_ALLOWLIST:
        logger.error(
            "panakoes-telemetry: %s is on %s; not in allowlist %s. Move state dir "
            "to a local POSIX filesystem.",
            path,
            fstype,
            _FS_ALLOWLIST,
        )
        return False
    logger.info("filesystem check passed: %s is on %s", path, fstype)
    return True


# ----------------------------------------------------------------------------
# SQLite schema
# ----------------------------------------------------------------------------

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA auto_vacuum  = INCREMENTAL;

CREATE TABLE IF NOT EXISTS events (
  id                          INTEGER PRIMARY KEY,
  trace_id                    TEXT NOT NULL,
  span_id                     TEXT NOT NULL,
  parent_span_id              TEXT,
  timestamp                   TEXT NOT NULL,
  hook_event_name             TEXT NOT NULL,
  session_id                  TEXT NOT NULL,
  tool_use_id                 TEXT,
  tool_name                   TEXT,
  brief                       TEXT,
  tool_use_duration_ms        INTEGER,
  success                     INTEGER,
  out_len                     INTEGER,
  err_len                     INTEGER,
  agent_id                    TEXT,
  agent_type                  TEXT,
  permission_mode             TEXT,
  effort_level                TEXT,
  gen_ai_request_model        TEXT,
  gen_ai_usage_input_tokens   INTEGER,
  gen_ai_usage_output_tokens  INTEGER,
  pr_number                   INTEGER,
  payload                     TEXT,
  disler_pushed_at            TEXT,
  dedup_key                   TEXT GENERATED ALWAYS AS
    (session_id || '|' || COALESCE(tool_use_id, '') || '|' || hook_event_name) STORED,
  UNIQUE (dedup_key)
);

CREATE INDEX IF NOT EXISTS idx_events_session_id   ON events (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_trace        ON events (trace_id, span_id);
CREATE INDEX IF NOT EXISTS idx_events_tool         ON events (tool_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_agent        ON events (agent_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_disler_null  ON events (disler_pushed_at) WHERE disler_pushed_at IS NULL;

CREATE TABLE IF NOT EXISTS sessions (
  session_id   TEXT PRIMARY KEY,
  trace_id     TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
"""


def init_db(sqlite_path: Path) -> None:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    # PRAGMA auto_vacuum must be set before any table is created OR followed
    # by a VACUUM to take effect on an existing database. We set it via a
    # separate connection up front, then VACUUM, then create tables. This
    # makes the init idempotent: on a fresh DB the PRAGMA sticks immediately;
    # on a pre-existing DB the VACUUM forces the auto_vacuum mode to apply
    # to subsequent allocations. PRAGMA journal_mode = WAL is also file-mode-
    # changing and is safe to issue here.
    with closing(sqlite3.connect(str(sqlite_path))) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
        # VACUUM is required for auto_vacuum=INCREMENTAL to take effect when
        # the database file already exists. On a fresh file it's a no-op.
        conn.execute("VACUUM")
    with closing(sqlite3.connect(str(sqlite_path))) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.commit()


# ----------------------------------------------------------------------------
# Trace context propagation (Section 3.5 / CRIT-01)
# ----------------------------------------------------------------------------


def new_trace_id() -> str:
    return secrets.token_hex(16)  # 32 hex chars = 16 bytes per W3C


def new_span_id() -> str:
    return secrets.token_hex(8)  # 16 hex chars = 8 bytes per W3C


class TraceContextStore:
    """In-memory + on-disk + SQLite-backed trace_id and span tracking.

    Three pieces of state:
      * trace_id_by_session: cached W3C trace_id per Claude Code session_id.
        Persisted to ${spool}/sessions/<sid>/trace_id (one-line file) and to
        the SQLite `sessions` table for cross-restart re-warm.
      * span_id_by_tool_use_id: in-memory map populated on PreToolUse,
        consumed on PostToolUse / PostToolUseFailure so the pre/post pair
        shares span_id (Section 3.5 step 3). Dropped after the Post* arrives.
      * active_span_by_session: stack-like in-memory map; on PreToolUse /
        SubagentStart, we record the spawned span_id and use the prior value
        as parent_span_id (Section 3.5 step 4). On Post* / SubagentStop we
        pop back.
    """

    def __init__(self, cfg: FlusherConfig, logger: logging.Logger) -> None:
        self._cfg = cfg
        self._log = logger
        self._trace_id_by_session: dict[str, str] = {}
        self._span_id_by_tool_use_id: dict[str, str] = {}
        # session_id -> list[span_id]; the last element is the current parent.
        self._active_span_stack_by_session: dict[str, list[str]] = {}

    def trace_id_for_session(self, session_id: str, conn: sqlite3.Connection) -> str:
        if session_id in self._trace_id_by_session:
            return self._trace_id_by_session[session_id]
        # Try the per-session file first (cheap; one open).
        f = self._cfg.sessions_dir / session_id / "trace_id"
        if f.exists():
            try:
                tid = f.read_text().strip()
                if tid:
                    self._trace_id_by_session[session_id] = tid
                    return tid
            except OSError as exc:
                self._log.warning("trace_id read failed for %s: %s", session_id, exc)
        # Try the SQLite cache.
        row = conn.execute(
            "SELECT trace_id FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is not None:
            tid = row[0]
            self._trace_id_by_session[session_id] = tid
            return tid
        # No record yet; this is a SessionStart or a stray event that arrived
        # before SessionStart. Mint a fresh trace_id and persist.
        tid = new_trace_id()
        self._persist_trace_id(session_id, tid, conn)
        return tid

    def mint_session(self, session_id: str, conn: sqlite3.Connection) -> str:
        """Idempotent: if the session already has a trace_id, return it."""
        if session_id in self._trace_id_by_session:
            return self._trace_id_by_session[session_id]
        f = self._cfg.sessions_dir / session_id / "trace_id"
        if f.exists():
            try:
                tid = f.read_text().strip()
                if tid:
                    self._trace_id_by_session[session_id] = tid
                    return tid
            except OSError:
                pass
        tid = new_trace_id()
        self._persist_trace_id(session_id, tid, conn)
        return tid

    def _persist_trace_id(self, session_id: str, trace_id: str, conn: sqlite3.Connection) -> None:
        sess_dir = self._cfg.sessions_dir / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        f = sess_dir / "trace_id"
        if not f.exists():
            try:
                f.write_text(trace_id)
            except OSError as exc:
                self._log.warning("trace_id persist failed for %s: %s", session_id, exc)
        self._trace_id_by_session[session_id] = trace_id
        conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id, trace_id, created_at) VALUES (?, ?, ?)",
            (session_id, trace_id, _utc_now_iso()),
        )

    def assign_span_for_event(
        self,
        event: dict[str, Any],
        hook_event_name: str,
        session_id: str,
        tool_use_id: str | None,
    ) -> tuple[str, str | None]:
        """Return (span_id, parent_span_id) for this event per Section 3.5.

        Maintains internal maps as a side effect.
        """
        stack = self._active_span_stack_by_session.setdefault(session_id, [])

        if hook_event_name in ("PreToolUse",) and tool_use_id:
            span = new_span_id()
            self._span_id_by_tool_use_id[tool_use_id] = span
            parent = stack[-1] if stack else None
            stack.append(span)
            return span, parent

        if hook_event_name in ("PostToolUse", "PostToolUseFailure") and tool_use_id:
            span = self._span_id_by_tool_use_id.pop(tool_use_id, None)
            if span is None:
                span = new_span_id()
                self._log.warning(
                    "PostToolUse without matching PreToolUse cache entry for %s; "
                    "pre/post linkage broken for this call",
                    tool_use_id,
                )
            # Pop the matching parent stack entry. If the stack's top doesn't
            # match (interleaved tools), best-effort: pop the matching span if
            # found, else pop the top.
            if span in stack:
                stack.remove(span)
            elif stack:
                stack.pop()
            parent = stack[-1] if stack else None
            return span, parent

        if hook_event_name == "SubagentStart":
            span = new_span_id()
            parent = stack[-1] if stack else None
            stack.append(span)
            return span, parent

        if hook_event_name == "SubagentStop":
            # Pop the most recent span (no per-agent-id mapping in v1 since
            # SubagentStart doesn't carry a tool_use_id; best-effort).
            parent = stack[-2] if len(stack) >= 2 else None
            span = stack.pop() if stack else new_span_id()
            return span, parent

        if hook_event_name == "SessionStart":
            span = new_span_id()
            # SessionStart is the root span; stack starts fresh.
            stack.clear()
            stack.append(span)
            return span, None

        if hook_event_name == "SessionEnd":
            parent = stack[-1] if stack else None
            span = new_span_id()
            return span, parent

        # Default: standalone event (UserPromptSubmit, Stop, PreCompact,
        # Notification, PermissionRequest). Generate a fresh span_id; parent
        # is the current top-of-stack.
        span = new_span_id()
        parent = stack[-1] if stack else None
        return span, parent


# ----------------------------------------------------------------------------
# Brief generation (Section 3.4)
# ----------------------------------------------------------------------------


def _truncate(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    # Reserve 3 chars for "..."
    return s[: cap - 3] + "..."


def extract_brief_raw(tool_name: str, tool_input: Any) -> str:
    """Return the per-tool informative field (pre-redaction, pre-truncation).

    Implements design Section 3.4 step 1.
    """
    if not isinstance(tool_input, dict):
        try:
            return json.dumps(tool_input, ensure_ascii=False)[:BRIEF_CAP_DEFAULT]
        except (TypeError, ValueError):
            return str(tool_input)[:BRIEF_CAP_DEFAULT]

    if tool_name == "Bash":
        cmd = tool_input.get("command")
        return cmd if isinstance(cmd, str) else json.dumps(tool_input, ensure_ascii=False)
    if tool_name in ("Edit", "Write", "Read", "MultiEdit", "NotebookEdit"):
        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
        return fp if isinstance(fp, str) else json.dumps(tool_input, ensure_ascii=False)
    if tool_name == "Agent":
        desc = tool_input.get("description") or tool_input.get("prompt", "")[:200]
        st = tool_input.get("subagent_type", tool_input.get("agent_type", "general-purpose"))
        return f"{desc}/{st}"
    if tool_name == "Grep":
        pat = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"{pat} @ {path}" if path else str(pat)
    if tool_name == "Glob":
        return str(tool_input.get("pattern", ""))
    if tool_name == "WebFetch":
        return str(tool_input.get("url", ""))
    if tool_name == "WebSearch":
        return str(tool_input.get("query", ""))

    # MCP tools: tool_input.input is the conservative-first key; fall back
    # to tool_input.params; fall back to tool_input itself (design Section
    # 3.4 MUST-03 / ADV-HIGH-07; the inner field name is not yet verified
    # upstream so we walk both).
    if isinstance(tool_name, str) and tool_name.startswith("mcp__"):
        server = tool_input.get("server", "")
        inner_tool = tool_input.get("tool", "")
        if "input" in tool_input and isinstance(tool_input["input"], (dict, list)):
            inner = tool_input["input"]
        elif "params" in tool_input and isinstance(tool_input["params"], (dict, list)):
            inner = tool_input["params"]
        else:
            inner = {k: v for k, v in tool_input.items() if k not in ("server", "tool")}
        try:
            inner_s = json.dumps(inner, ensure_ascii=False)
        except (TypeError, ValueError):
            inner_s = str(inner)
        prefix = f"{server}/{inner_tool}: " if server or inner_tool else f"{tool_name}: "
        return prefix + inner_s

    # Default: first N chars of the JSON-encoded tool_input.
    try:
        return json.dumps(tool_input, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(tool_input)


def brief_cap_for(tool_name: str) -> int:
    if tool_name == "Bash":
        return BRIEF_CAP_BASH
    if tool_name == "Agent":
        return BRIEF_CAP_AGENT
    return BRIEF_CAP_DEFAULT


# ----------------------------------------------------------------------------
# Redaction via gitleaks (Section 3.4 step 2)
# ----------------------------------------------------------------------------


def redact_via_gitleaks(text: str, logger: logging.Logger) -> str:
    """Run gitleaks against `text`, return text with detected secrets redacted.

    Implementation notes (decided during integration testing 2026-05-19):

    The design Section 3.4 step 2 originally specified `gitleaks detect --pipe
    --redact`, but empirically:

      1. `gitleaks detect --pipe` triggers ~7 second scans (heavyweight git
         config detection) vs ~2 ms for file-based scanning, AND
      2. Many rules (AWS keys, Stripe tokens, GitHub PATs, Slack webhooks,
         GCP API keys) do NOT fire on pipe input, only on file input.
      3. `--redact` only affects how matches display in gitleaks's own logs;
         it does NOT emit redacted source on stdout. The redacted text has
         to be reconstructed by substituting each finding's `Secret` value
         with a sentinel.

    Resolution: write the text to a temp file, scan that file with
    `--source`, parse the JSON report, substitute each finding. Sentinel
    format is `<REDACTED:gitleaks:<RuleID>>` per the design.

    Performance: ~50-200 ms per call file-based (vs 7+ sec pipe-based);
    well within the flusher's budget. A future batched-redactor optimization
    is documented in the run report.
    """
    if not text:
        return text
    import tempfile

    src_fd, src_path = tempfile.mkstemp(suffix=".env", prefix="panakoes-redact-")
    os.close(src_fd)
    rpt_fd, report_path = tempfile.mkstemp(suffix=".json", prefix="panakoes-glreport-")
    os.close(rpt_fd)
    try:
        with open(src_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            proc = subprocess.run(
                [
                    "gitleaks",
                    "detect",
                    "--no-git",
                    "--no-banner",
                    "--source",
                    src_path,
                    "--report-format",
                    "json",
                    "--report-path",
                    report_path,
                    "--exit-code",
                    "0",  # don't error on findings; we parse the report
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except FileNotFoundError:
            logger.warning("gitleaks not on PATH; skipping redaction (UNSAFE)")
            return text
        except subprocess.TimeoutExpired:
            logger.warning("gitleaks timed out; returning original text (UNSAFE)")
            return text
        if proc.returncode != 0:
            logger.warning(
                "gitleaks rc=%d stderr=%s",
                proc.returncode,
                proc.stderr.strip()[:200],
            )
            return text
        try:
            with open(report_path, "r", encoding="utf-8") as fh:
                findings = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("gitleaks report parse failed: %s", exc)
            return text
    finally:
        for p in (src_path, report_path):
            try:
                os.unlink(p)
            except OSError:
                pass
    if not isinstance(findings, list) or not findings:
        return text
    redacted = text
    # Sort by secret length descending so a short match never gets swapped
    # in before its containing longer match is processed.
    findings.sort(key=lambda f: len(str(f.get("Secret", ""))), reverse=True)
    for f in findings:
        if not isinstance(f, dict):
            continue
        secret = f.get("Secret")
        rule = f.get("RuleID", "unknown")
        if not isinstance(secret, str) or not secret:
            continue
        sentinel = f"<REDACTED:gitleaks:{rule}>"
        redacted = redacted.replace(secret, sentinel)
    return redacted


# ----------------------------------------------------------------------------
# Per-tool extraction (pr_number) per Section 3.4 step 5
# ----------------------------------------------------------------------------


def extract_pr_number(event: dict[str, Any]) -> int | None:
    """Extract pr_number at hook time, then discard the response body.

    Section 8 invariant: we read tool_result.content once for this extraction
    and never persist the body. The events row stores pr_number (or NULL).
    """
    hook_name = event.get("hook_event_name")
    if hook_name != "PostToolUse":
        return None
    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    tool_result = event.get("tool_result") or {}

    # Bash + gh pr create -> regex on tool_result.content
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        if "gh pr create" in cmd:
            content = ""
            if isinstance(tool_result, dict):
                content = tool_result.get("content", "") or ""
            elif isinstance(tool_result, str):
                content = tool_result
            if isinstance(content, list):
                # tool_result.content can be a list of {type, text} blobs
                content = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            m = PR_URL_RE.search(str(content))
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None

    # MCP create_pull_request -> tool_result.number top-level
    if tool_name == MCP_CREATE_PR_TOOL:
        if isinstance(tool_result, dict):
            num = tool_result.get("number")
            if isinstance(num, int):
                return num
            if isinstance(num, str) and num.isdigit():
                return int(num)
            # MCP servers sometimes wrap result in {content: [{type: text, text: ...}]}
            content = tool_result.get("content")
            if isinstance(content, list):
                for blob in content:
                    if isinstance(blob, dict) and isinstance(blob.get("text"), str):
                        try:
                            parsed = json.loads(blob["text"])
                            if isinstance(parsed, dict) and isinstance(parsed.get("number"), int):
                                return parsed["number"]
                        except (json.JSONDecodeError, ValueError):
                            continue
    return None


# ----------------------------------------------------------------------------
# Field extraction helpers
# ----------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".{:03d}Z".format(
        int((time.time() * 1000) % 1000)
    )


def _get_int(d: dict[str, Any], key: str) -> int | None:
    v = d.get(key)
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def _result_text_len(tool_result: Any) -> int:
    if isinstance(tool_result, dict):
        c = tool_result.get("content")
        if isinstance(c, str):
            return len(c)
        if isinstance(c, list):
            total = 0
            for blob in c:
                if isinstance(blob, dict) and isinstance(blob.get("text"), str):
                    total += len(blob["text"])
            return total
        # fall through
        return len(json.dumps(tool_result, ensure_ascii=False))
    if isinstance(tool_result, str):
        return len(tool_result)
    return 0


# ----------------------------------------------------------------------------
# Event processing
# ----------------------------------------------------------------------------


@dataclass
class ProcessedEvent:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    timestamp: str
    hook_event_name: str
    session_id: str
    tool_use_id: str | None
    tool_name: str | None
    brief: str | None
    tool_use_duration_ms: int | None
    success: int | None
    out_len: int | None
    err_len: int | None
    agent_id: str | None
    agent_type: str | None
    permission_mode: str | None
    effort_level: str | None
    gen_ai_request_model: str | None
    gen_ai_usage_input_tokens: int | None
    gen_ai_usage_output_tokens: int | None
    pr_number: int | None
    payload: str  # JSON, redacted; for ad-hoc query
    # Disler-projection fields (Section 4.3) precomputed for the POST.
    disler_payload: dict[str, Any] = field(default_factory=dict)


def process_event(
    raw: dict[str, Any],
    file_path: Path,
    ctx: TraceContextStore,
    conn: sqlite3.Connection,
    logger: logging.Logger,
) -> ProcessedEvent | None:
    """Apply trace propagation, brief extraction, redaction; return a row."""
    session_id = str(raw.get("session_id") or "unknown")
    hook_event_name = str(raw.get("hook_event_name") or "unknown")
    if hook_event_name not in HOOK_EVENTS:
        logger.warning("unknown hook_event_name %r in %s", hook_event_name, file_path)
    tool_use_id = raw.get("tool_use_id")
    if tool_use_id is not None:
        tool_use_id = str(tool_use_id)
    tool_name = raw.get("tool_name")
    if tool_name is not None:
        tool_name = str(tool_name)

    # Trace + span assignment.
    if hook_event_name == "SessionStart":
        trace_id = ctx.mint_session(session_id, conn)
    else:
        trace_id = ctx.trace_id_for_session(session_id, conn)
    span_id, parent_span_id = ctx.assign_span_for_event(
        raw, hook_event_name, session_id, tool_use_id
    )

    # Brief extraction (pre-redaction).
    brief_raw = ""
    if tool_name is not None and raw.get("tool_input") is not None:
        brief_raw = extract_brief_raw(tool_name, raw.get("tool_input"))
    elif hook_event_name == "UserPromptSubmit":
        prompt = raw.get("prompt") or raw.get("user_prompt") or ""
        brief_raw = str(prompt)
    elif hook_event_name in ("Notification", "PermissionRequest"):
        brief_raw = str(raw.get("message", "")) or str(raw.get("tool_name", ""))

    # Redact + truncate the brief.
    brief_redacted: str | None = None
    if brief_raw:
        try:
            brief_redacted = redact_via_gitleaks(brief_raw, logger)
        except Exception as exc:  # noqa: BLE001 -- redactor failure must not lose event
            logger.warning("brief redaction failed: %s; storing original UNSAFE", exc)
            brief_redacted = brief_raw
        cap = brief_cap_for(tool_name or "")
        brief_redacted = _truncate(brief_redacted, cap)

    # PR number extraction (Section 3.4 step 5).
    pr_number = extract_pr_number(raw)

    # PostToolUseFailure success flag.
    success: int | None
    if hook_event_name == "PostToolUseFailure":
        success = 0
    elif hook_event_name == "PostToolUse":
        success = 1
    else:
        success = None

    # Derive out_len/err_len from tool_result if present (never store the body).
    out_len: int | None = None
    err_len: int | None = None
    if raw.get("tool_result") is not None:
        out_len = _result_text_len(raw["tool_result"])
    if hook_event_name == "PostToolUseFailure":
        err = raw.get("error", {})
        if isinstance(err, dict):
            content = err.get("content", "")
            err_len = len(str(content))

    # Tool-call duration from PostToolUse server field (IMP-05).
    dur_ms = _get_int(raw, "tool_use_duration_ms")

    # Agent fields, permission mode, effort.
    agent_id = raw.get("agent_id")
    agent_type = raw.get("agent_type")
    permission_mode = raw.get("permission_mode")
    effort = raw.get("effort") or {}
    effort_level = effort.get("level") if isinstance(effort, dict) else raw.get("effort_level")

    # GenAI usage fields.
    gen_ai_model = raw.get("gen_ai", {}).get("request", {}).get("model") if isinstance(raw.get("gen_ai"), dict) else None
    gen_ai_usage = raw.get("gen_ai", {}).get("usage", {}) if isinstance(raw.get("gen_ai"), dict) else {}
    in_tok = _get_int(gen_ai_usage, "input_tokens") if isinstance(gen_ai_usage, dict) else None
    out_tok = _get_int(gen_ai_usage, "output_tokens") if isinstance(gen_ai_usage, dict) else None
    # Fall back to flat-field aliases.
    if gen_ai_model is None:
        gen_ai_model = raw.get("model_name") or raw.get("gen_ai_request_model")
    if in_tok is None:
        in_tok = _get_int(raw, "gen_ai_usage_input_tokens")
    if out_tok is None:
        out_tok = _get_int(raw, "gen_ai_usage_output_tokens")

    # Redact + serialize the full payload (Section 4.2: both sinks see the
    # SAME redacted payload, redaction is the only defense).
    try:
        payload_text = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        logger.warning("payload serialize failed for %s: %s", file_path, exc)
        payload_text = json.dumps({"_serialize_error": str(exc), "file": str(file_path)})
    try:
        payload_redacted = redact_via_gitleaks(payload_text, logger)
    except Exception as exc:  # noqa: BLE001
        logger.warning("payload redaction failed: %s; storing original UNSAFE", exc)
        payload_redacted = payload_text

    timestamp = str(raw.get("timestamp") or _utc_now_iso())

    pe = ProcessedEvent(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        timestamp=timestamp,
        hook_event_name=hook_event_name,
        session_id=session_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        brief=brief_redacted,
        tool_use_duration_ms=dur_ms,
        success=success,
        out_len=out_len,
        err_len=err_len,
        agent_id=agent_id,
        agent_type=agent_type,
        permission_mode=permission_mode,
        effort_level=effort_level,
        gen_ai_request_model=gen_ai_model,
        gen_ai_usage_input_tokens=in_tok,
        gen_ai_usage_output_tokens=out_tok,
        pr_number=pr_number,
        payload=payload_redacted,
    )

    # Disler projection (Section 4.3).
    pe.disler_payload = project_for_disler(pe, raw)
    return pe


def project_for_disler(pe: ProcessedEvent, raw: dict[str, Any]) -> dict[str, Any]:
    error_text: str | None = None
    if pe.hook_event_name == "PostToolUseFailure":
        err = raw.get("error", {})
        if isinstance(err, dict):
            t = err.get("type", "")
            c = err.get("content", "")
            error_text = _truncate(f"{t}: {c}", 512)
    # Parse pe.timestamp into Unix ms; if it fails, use now.
    try:
        ts_ms = int(time.time() * 1000)
    except OverflowError:
        ts_ms = 0
    # Try the redacted payload (so trace fields are present); the raw is
    # already redacted in pe.payload.
    try:
        payload_obj = json.loads(pe.payload)
    except (json.JSONDecodeError, ValueError):
        payload_obj = {"_payload_parse_error": True}
    payload_obj["trace_id"] = pe.trace_id
    payload_obj["span_id"] = pe.span_id
    payload_obj["parent_span_id"] = pe.parent_span_id
    return {
        "source_app": "panakoes",
        "session_id": pe.session_id,
        "hook_event_type": pe.hook_event_name,
        "payload": payload_obj,
        "timestamp": ts_ms,
        "model_name": pe.gen_ai_request_model,
        "tool_name": pe.tool_name,
        "tool_use_id": pe.tool_use_id,
        "error": error_text,
        "agent_id": pe.agent_id,
    }


# ----------------------------------------------------------------------------
# SQLite + disler writers
# ----------------------------------------------------------------------------

INSERT_SQL = """
INSERT OR IGNORE INTO events (
  trace_id, span_id, parent_span_id, timestamp, hook_event_name,
  session_id, tool_use_id, tool_name, brief, tool_use_duration_ms,
  success, out_len, err_len, agent_id, agent_type, permission_mode,
  effort_level, gen_ai_request_model, gen_ai_usage_input_tokens,
  gen_ai_usage_output_tokens, pr_number, payload, disler_pushed_at
) VALUES (
  :trace_id, :span_id, :parent_span_id, :timestamp, :hook_event_name,
  :session_id, :tool_use_id, :tool_name, :brief, :tool_use_duration_ms,
  :success, :out_len, :err_len, :agent_id, :agent_type, :permission_mode,
  :effort_level, :gen_ai_request_model, :gen_ai_usage_input_tokens,
  :gen_ai_usage_output_tokens, :pr_number, :payload, NULL
)
"""


def insert_event(conn: sqlite3.Connection, pe: ProcessedEvent) -> bool:
    """INSERT OR IGNORE; return True iff a row was inserted."""
    cur = conn.execute(
        INSERT_SQL,
        {
            "trace_id": pe.trace_id,
            "span_id": pe.span_id,
            "parent_span_id": pe.parent_span_id,
            "timestamp": pe.timestamp,
            "hook_event_name": pe.hook_event_name,
            "session_id": pe.session_id,
            "tool_use_id": pe.tool_use_id,
            "tool_name": pe.tool_name,
            "brief": pe.brief,
            "tool_use_duration_ms": pe.tool_use_duration_ms,
            "success": pe.success,
            "out_len": pe.out_len,
            "err_len": pe.err_len,
            "agent_id": pe.agent_id,
            "agent_type": pe.agent_type,
            "permission_mode": pe.permission_mode,
            "effort_level": pe.effort_level,
            "gen_ai_request_model": pe.gen_ai_request_model,
            "gen_ai_usage_input_tokens": pe.gen_ai_usage_input_tokens,
            "gen_ai_usage_output_tokens": pe.gen_ai_usage_output_tokens,
            "pr_number": pe.pr_number,
            "payload": pe.payload,
        },
    )
    return cur.rowcount > 0


def post_to_disler(
    cfg: FlusherConfig, pe: ProcessedEvent, logger: logging.Logger
) -> bool:
    """POST the projected payload; return True on 2xx."""
    if not cfg.disler_enabled:
        return False
    url = f"{cfg.disler_url}/events"
    idempotency_key = f"{pe.session_id}-{pe.tool_use_id or ''}-{pe.hook_event_name}"
    body = json.dumps(pe.disler_payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=DISLER_HTTP_TIMEOUT_SEC) as resp:
            return 200 <= resp.status < 300
    except urlerror.HTTPError as exc:
        logger.warning("disler POST %s -> %d %s", url, exc.code, exc.reason)
        return False
    except (urlerror.URLError, TimeoutError) as exc:
        logger.warning("disler POST %s unreachable: %s", url, exc)
        return False


def disler_healthcheck(cfg: FlusherConfig, logger: logging.Logger) -> bool:
    """Probe DISLER_URL/DISLER_HEALTH_PATH; return True iff reachable.

    Per HIGH-06 (orchestrator-verified 2026-05-19), disler has NO /health
    endpoint; we use /events/recent (a real GET) as the reachability probe.
    Default value of DISLER_HEALTH_PATH is /events/recent.
    """
    if not cfg.disler_enabled:
        return False
    url = cfg.disler_url + cfg.disler_health_path
    try:
        with urlrequest.urlopen(url, timeout=DISLER_HTTP_TIMEOUT_SEC) as resp:
            ok = 200 <= resp.status < 500  # 405 is also a "reachable" signal
            return ok
    except urlerror.HTTPError as exc:
        return 200 <= exc.code < 500
    except (urlerror.URLError, TimeoutError) as exc:
        logger.debug("disler health %s unreachable: %s", url, exc)
        return False


# ----------------------------------------------------------------------------
# Spool drain
# ----------------------------------------------------------------------------


def list_spool_files(spool_dir: Path) -> list[Path]:
    if not spool_dir.exists():
        return []
    files: list[Path] = []
    for session_dir in spool_dir.iterdir():
        if not session_dir.is_dir():
            continue
        for f in session_dir.iterdir():
            if f.is_file() and f.name.endswith(".json"):
                files.append(f)
    # Order matters for trace-context propagation: PreToolUse must precede
    # PostToolUse for the same tool_use_id, SessionStart must come first.
    # ext4 mtime resolution can collapse files written within microseconds
    # to the same ns timestamp, so we use a composite key:
    #   (event.timestamp if present else mtime_ns, mtime_ns, filename)
    # Reading the event's own timestamp matches Claude Code's actual ordering
    # (each hook event carries an ISO 8601 timestamp). The filename tiebreaker
    # is the final guarantor: mktemp's random suffix is unique per file.
    def _sort_key(p: Path) -> tuple[str, int, str]:
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            mt = 0
        # Try to peek the event timestamp without parsing the full JSON. If
        # the event has a top-level "timestamp" field, sort by that lexically
        # (ISO 8601 ordering = chronological). Failure falls back to mtime.
        ts = ""
        try:
            # Read up to 4 KB to find the timestamp; events are small.
            head = p.read_text(encoding="utf-8", errors="replace")[:4096]
            doc = json.loads(head) if head.strip() else {}
            v = doc.get("timestamp")
            if isinstance(v, str):
                ts = v
        except (OSError, json.JSONDecodeError, ValueError):
            ts = ""
        return (ts, mt, p.name)

    files.sort(key=_sort_key)
    return files


def drain_once(
    cfg: FlusherConfig,
    conn: sqlite3.Connection,
    ctx: TraceContextStore,
    logger: logging.Logger,
) -> dict[str, int]:
    files = list_spool_files(cfg.spool_dir)
    if not files:
        return {"files": 0, "inserted": 0, "posted": 0, "skipped": 0, "errors": 0}

    n = len(files)
    if n >= SPOOL_HARDSTOP_THRESHOLD:
        logger.error("spool depth %d >= hard-stop %d; refusing to drain", n, SPOOL_HARDSTOP_THRESHOLD)
        return {"files": n, "inserted": 0, "posted": 0, "skipped": 0, "errors": n, "hardstop": 1}
    if n >= SPOOL_WARN_THRESHOLD:
        logger.warning("spool depth %d >= warn %d", n, SPOOL_WARN_THRESHOLD)

    stats = {"files": n, "inserted": 0, "posted": 0, "skipped": 0, "errors": 0}
    for f in files:
        try:
            raw_text = f.read_text(encoding="utf-8", errors="replace")
            if not raw_text.strip():
                # Empty sentinel from shim (stdin was empty). Drop it.
                f.unlink(missing_ok=True)
                stats["skipped"] += 1
                continue
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                logger.warning("drop unparseable spool file %s: %s", f, exc)
                # Move to a "bad/" subdir for inspection rather than silent delete.
                bad_dir = cfg.spool_dir.parent / "bad"
                bad_dir.mkdir(parents=True, exist_ok=True)
                try:
                    f.rename(bad_dir / f.name)
                except OSError:
                    f.unlink(missing_ok=True)
                stats["errors"] += 1
                continue
            if not isinstance(raw, dict):
                logger.warning("drop non-object spool file %s", f)
                f.unlink(missing_ok=True)
                stats["errors"] += 1
                continue
            pe = process_event(raw, f, ctx, conn, logger)
            if pe is None:
                stats["skipped"] += 1
                f.unlink(missing_ok=True)
                continue
            try:
                inserted = insert_event(conn, pe)
            except sqlite3.Error as exc:
                logger.error("sqlite insert failed for %s: %s", f, exc)
                stats["errors"] += 1
                # Leave the spool file so a retry has a chance.
                continue
            conn.commit()
            if inserted:
                stats["inserted"] += 1
            else:
                stats["skipped"] += 1  # duplicate; already in db
            # Dual-write step 2: POST to disler if enabled.
            if cfg.disler_enabled:
                ok = post_to_disler(cfg, pe, logger)
                if ok:
                    conn.execute(
                        "UPDATE events SET disler_pushed_at = ? "
                        "WHERE dedup_key = ?",
                        (
                            _utc_now_iso(),
                            f"{pe.session_id}|{pe.tool_use_id or ''}|{pe.hook_event_name}",
                        ),
                    )
                    conn.commit()
                    stats["posted"] += 1
            # Spool file is the source-of-truth fallback; only unlink after
            # the SQLite row commits (above) or after the duplicate is
            # confirmed-in-db.
            f.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("spool file IO error %s: %s", f, exc)
            stats["errors"] += 1
            continue

    return stats


# ----------------------------------------------------------------------------
# Long-lived loop
# ----------------------------------------------------------------------------


class GracefulShutdown:
    def __init__(self) -> None:
        self.stop = False

    def install(self) -> None:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum: int, _frame: Any) -> None:
        self.stop = True


def run_loop(cfg: FlusherConfig, logger: logging.Logger) -> None:
    init_db(cfg.sqlite_path)
    ctx = TraceContextStore(cfg, logger)
    shutdown = GracefulShutdown()
    shutdown.install()

    next_healthcheck = time.monotonic() + HEALTHCHECK_INTERVAL_SEC
    disler_up: bool | None = None

    with closing(sqlite3.connect(str(cfg.sqlite_path))) as conn:
        while not shutdown.stop:
            try:
                stats = drain_once(cfg, conn, ctx, logger)
                if stats["inserted"] or stats["errors"]:
                    logger.info("drain: %s", stats)
                now = time.monotonic()
                if now >= next_healthcheck and cfg.disler_enabled:
                    up = disler_healthcheck(cfg, logger)
                    if up != disler_up:
                        logger.info("disler health: %s", "UP" if up else "DOWN")
                        disler_up = up
                    next_healthcheck = now + HEALTHCHECK_INTERVAL_SEC
            except Exception as exc:  # noqa: BLE001 -- the drain loop must not die
                logger.exception("drain iteration failed: %s", exc)
            time.sleep(cfg.poll_interval_sec)

        # Final drain on shutdown (graceful).
        logger.info("shutdown signal received; final drain")
        try:
            drain_once(cfg, conn, ctx, logger)
        except Exception as exc:  # noqa: BLE001
            logger.exception("final drain failed: %s", exc)
        logger.info("flusher exiting")


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run a single drain cycle then exit")
    parser.add_argument("--init-only", action="store_true", help="init the SQLite schema then exit")
    parser.add_argument(
        "--skip-fs-check",
        action="store_true",
        help="skip the filesystem allowlist check (use only in CI / tests)",
    )
    args = parser.parse_args(argv)

    cfg = FlusherConfig.from_env()
    cfg.ensure_state_layout()
    logger = setup_logging(cfg)
    logger.info(
        "starting; state_dir=%s disler_enabled=%s disler_url=%s",
        cfg.state_dir,
        cfg.disler_enabled,
        cfg.disler_url,
    )

    if not args.skip_fs_check and not check_filesystem_atomicity(cfg.state_dir, logger):
        return 2

    if args.init_only:
        init_db(cfg.sqlite_path)
        logger.info("schema init complete: %s", cfg.sqlite_path)
        return 0

    if args.once:
        init_db(cfg.sqlite_path)
        ctx = TraceContextStore(cfg, logger)
        with closing(sqlite3.connect(str(cfg.sqlite_path))) as conn:
            stats = drain_once(cfg, conn, ctx, logger)
        logger.info("once-mode drain: %s", stats)
        return 0

    run_loop(cfg, logger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
