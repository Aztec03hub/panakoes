"""test_atomic_append: prove the shim's mktemp pattern is collision-free under
high concurrency.

Per design Section 3.7 (MUST-04 / ADV-HIGH-02): the shim writes one file per
event via mktemp (O_CREAT|O_EXCL semantics, random 10-char suffix). No two
concurrent shim invocations can ever target the same path; cross-write
interleaving is structurally impossible.

This test fires N parallel shim invocations and asserts:
  1. exactly N files land in the spool dir
  2. every file parses as valid JSON
  3. every file holds exactly one event (the one we sent)
  4. no two files share a name
  5. no JSON content is truncated or interleaved
"""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest


N_PARALLEL = 100


def _fire_one(shim: Path, env: dict[str, str], index: int) -> int:
    payload = json.dumps(
        {
            "session_id": "atomic-test",
            "tool_use_id": f"toolu_atomic_{index:04d}",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"echo atomic-iter-{index}"},
        }
    )
    res = subprocess.run(
        [str(shim)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )
    return res.returncode


def test_shim_handles_100_parallel_writes_without_collision(
    telemetry_env: Path, shim_path: Path
) -> None:
    """Section 3.7 atomicity guarantee: 100 parallel invocations -> 100 files."""
    env = {**os.environ, "PANAKOES_TELEMETRY_DIR": str(telemetry_env)}

    with ThreadPoolExecutor(max_workers=32) as ex:
        futures = [ex.submit(_fire_one, shim_path, env, i) for i in range(N_PARALLEL)]
        rcs = [f.result() for f in as_completed(futures)]

    # All shim invocations exit 0 unconditionally per Section 8 invariant.
    assert all(rc == 0 for rc in rcs), f"some shim invocations returned non-zero: {set(rcs)}"

    spool_dir = telemetry_env / "spool" / "atomic-test"
    assert spool_dir.is_dir(), "spool dir was not created"

    files = list(spool_dir.glob("*.json"))
    assert len(files) == N_PARALLEL, (
        f"expected {N_PARALLEL} spool files, got {len(files)}; "
        f"collisions or write failures occurred"
    )

    # No duplicate filenames (mktemp's job).
    names = {f.name for f in files}
    assert len(names) == N_PARALLEL, "duplicate spool filenames -> mktemp collision"

    # Every file parses as JSON and contains the expected single event.
    seen_indices: set[int] = set()
    for f in files:
        content = f.read_text(encoding="utf-8")
        try:
            doc = json.loads(content)
        except json.JSONDecodeError as exc:
            pytest.fail(f"file {f.name} is not valid JSON: {exc}\ncontent: {content!r}")
        assert isinstance(doc, dict), f"file {f.name} should hold a dict, got {type(doc)}"
        tu = doc.get("tool_use_id", "")
        assert tu.startswith("toolu_atomic_"), f"unexpected tool_use_id {tu}"
        idx = int(tu.rsplit("_", 1)[-1])
        assert idx not in seen_indices, f"duplicate event index {idx} across files"
        seen_indices.add(idx)

    assert seen_indices == set(range(N_PARALLEL)), (
        "missing event indices: " + str(set(range(N_PARALLEL)) - seen_indices)
    )


def test_shim_handles_events_without_tool_use_id(
    telemetry_env: Path, shim_path: Path
) -> None:
    """SessionStart / UserPromptSubmit / etc. have no tool_use_id; the synthetic
    id-part (per LOW-06 / HIGH-02) must still produce collision-free files."""
    env = {**os.environ, "PANAKOES_TELEMETRY_DIR": str(telemetry_env)}
    payload = json.dumps(
        {"session_id": "no-tuid-test", "hook_event_name": "SessionStart"}
    )
    N = 30
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [
            ex.submit(
                subprocess.run,
                [str(shim_path)],
                input=payload,
                capture_output=True,
                text=True,
                env=env,
                check=False,
                timeout=10,
            )
            for _ in range(N)
        ]
        results = [f.result() for f in as_completed(futures)]
    assert all(r.returncode == 0 for r in results)
    spool_dir = telemetry_env / "spool" / "no-tuid-test"
    files = list(spool_dir.glob("*.json"))
    assert len(files) == N, (
        f"expected {N} files for SessionStart, got {len(files)}; "
        "synthetic id-part collision"
    )
