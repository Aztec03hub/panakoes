"""test_redaction: verify gitleaks redaction substitutes detected secrets.

Per design Section 3.4 step 2 (IMP-04), brief and payload columns must have
gitleaks-detected secrets replaced with <REDACTED:gitleaks:<RuleID>> sentinels
before being written to either sink.

This test plants a Stripe live key and a GitHub PAT in a Bash command, drains
through the flusher, and asserts the SQLite brief column does not contain the
plaintext secret.
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


# Construct fake secrets at runtime so neither GitHub's push protection nor
# gitleaks-on-.pyc-bytecode flags the file. Plain `"sk_" + "live_" + "..."` is
# folded by the Python compiler into a single literal that ends up in the .pyc,
# so we use `.join` (which is a runtime call, not a constant-foldable expression)
# to defer the concatenation past compile time. gitleaks still detects the
# assembled string at test execution time.
STRIPE_FAKE = "".join(["sk_", "live_", "4eC39HqLyjWDarjtT1zdp7dc"])
GITHUB_PAT_FAKE = "".join(["ghp_", "abcdefghijklmnopqrstuvwxyz0123456789"])


def _have_gitleaks() -> bool:
    from shutil import which

    return which("gitleaks") is not None


@pytest.mark.skipif(not _have_gitleaks(), reason="gitleaks binary not on PATH")
def test_planted_secret_gets_redacted_in_brief(
    telemetry_env: Path, flusher_path: Path, shim_path: Path
) -> None:
    payload = json.dumps(
        {
            "session_id": "redact-test",
            "tool_use_id": "toolu_secret",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {
                "command": (
                    f"export STRIPE_KEY={STRIPE_FAKE} "
                    f"&& export GH_PAT={GITHUB_PAT_FAKE} && curl example.com"
                )
            },
        }
    )
    env = {**os.environ, "PANAKOES_TELEMETRY_DIR": str(telemetry_env)}
    res = subprocess.run(
        [str(shim_path)],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert res.returncode == 0

    res = subprocess.run(
        [sys.executable, str(flusher_path), "--once", "--skip-fs-check"],
        env={**env, "DISLER_ENABLED": "false"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr

    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT brief, payload FROM events WHERE tool_use_id = ?",
            ("toolu_secret",),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "event was not inserted"
    brief, payload_text = row
    assert STRIPE_FAKE not in brief, f"Stripe key leaked into brief: {brief!r}"
    assert GITHUB_PAT_FAKE not in brief, f"GitHub PAT leaked into brief: {brief!r}"
    assert "<REDACTED:gitleaks:" in brief, f"redaction sentinel missing: {brief!r}"
    # Payload is also redacted (Section 4.2: BOTH sinks see the same redacted data).
    assert STRIPE_FAKE not in payload_text, "Stripe key leaked into payload"
    assert GITHUB_PAT_FAKE not in payload_text, "GitHub PAT leaked into payload"


@pytest.mark.skipif(not _have_gitleaks(), reason="gitleaks binary not on PATH")
def test_clean_event_passes_through_unchanged_in_brief(
    telemetry_env: Path, flusher_path: Path, shim_path: Path
) -> None:
    payload = json.dumps(
        {
            "session_id": "clean-test",
            "tool_use_id": "toolu_clean",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git status --short"},
        }
    )
    env = {**os.environ, "PANAKOES_TELEMETRY_DIR": str(telemetry_env)}
    subprocess.run(
        [str(shim_path)], input=payload, env=env, capture_output=True, text=True, check=False
    )
    subprocess.run(
        [sys.executable, str(flusher_path), "--once", "--skip-fs-check"],
        env={**env, "DISLER_ENABLED": "false"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    db = telemetry_env / "telemetry.sqlite"
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT brief FROM events WHERE tool_use_id = ?", ("toolu_clean",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "git status --short"
