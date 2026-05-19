"""pytest fixtures for the telemetry test suite.

We expose a `telemetry_env` fixture that creates an isolated
PANAKOES_TELEMETRY_DIR per test (tmp_path-based), so concurrent tests do not
collide on the spool / SQLite file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SHIM_PATH = REPO_ROOT / ".claude" / "hooks" / "trace-shim.sh"
FLUSHER_PATH = REPO_ROOT / "scripts" / "telemetry-flusher.py"


@pytest.fixture
def telemetry_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "panakoes-telemetry"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PANAKOES_TELEMETRY_DIR", str(state))
    monkeypatch.setenv("DISLER_ENABLED", "false")
    return state


@pytest.fixture
def shim_path() -> Path:
    if not SHIM_PATH.exists():
        pytest.skip(f"shim not found at {SHIM_PATH}")
    return SHIM_PATH


@pytest.fixture
def flusher_path() -> Path:
    if not FLUSHER_PATH.exists():
        pytest.skip(f"flusher not found at {FLUSHER_PATH}")
    return FLUSHER_PATH
