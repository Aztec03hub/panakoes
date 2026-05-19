#!/usr/bin/env python3
"""pr-monitor.py: emit one event line per meaningful PR / CI state change.

Designed to be wrapped by Claude Code's Monitor tool. Each stdout line is
an event. Detects:
  - PR state transitions (OPEN/MERGED/CLOSED)
  - mergeStateStatus changes (BEHIND/DIRTY/CLEAN/UNSTABLE)
  - per-check failures (CI-FAIL) and recoveries (CI-RECOVERED)
  - STALLED checks (pending > PR_MONITOR_STALL_MIN minutes; emitted ONCE)
  - heartbeats every PR_MONITOR_HEARTBEAT polls (default: every ~5 min)

Liveness sidecar:
  - PR_MONITOR_LASTPOLL written every poll with ISO-8601 timestamp.
  - Read this file to verify the monitor is alive without waiting for events.

Env vars:
  PR_MONITOR_REPO          (default "Aztec03hub/panakoes")
  PR_MONITOR_INTERVAL      seconds between polls (default 30)
  PR_MONITOR_HEARTBEAT     emit a heartbeat every N polls (default 10)
  PR_MONITOR_STALL_MIN     emit STALLED if a check is pending > this (default 15)
  PR_MONITOR_STATE         state file path (default /tmp/pr-monitor-state-<pid>.json)
  PR_MONITOR_LASTPOLL      liveness file path (default <state>.lastpoll)
  PR_MONITOR_QUIET         if "1", drop heartbeat (changes only)
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

REPO     = os.environ.get("PR_MONITOR_REPO", "Aztec03hub/panakoes")
INTERVAL = int(os.environ.get("PR_MONITOR_INTERVAL", "30"))
HEARTBEAT_POLLS = int(os.environ.get("PR_MONITOR_HEARTBEAT", "10"))
STALL_MIN       = int(os.environ.get("PR_MONITOR_STALL_MIN", "15"))
QUIET   = os.environ.get("PR_MONITOR_QUIET", "0") == "1"
STATE   = Path(os.environ.get("PR_MONITOR_STATE", f"/tmp/pr-monitor-state-{os.getpid()}.json"))
LASTPOLL = Path(os.environ.get("PR_MONITOR_LASTPOLL", str(STATE).replace(".json", ".lastpoll")))

def now_hms(): return datetime.now(timezone.utc).strftime("%H:%M:%SZ")
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="seconds")

def emit(line: str):
    print(f"{now_hms()} {line}", flush=True)

def snapshot():
    """Pull current PR + check state. Returns list of PR dicts, or None on error."""
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", REPO, "--state", "all", "--limit", "30",
         "--json", "number,state,mergeStateStatus,title,statusCheckRollup"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return None
    try:
        raw = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return None
    out = []
    for pr in raw:
        rollup = pr.get("statusCheckRollup") or []
        checks = []
        for c in rollup:
            # Both CheckRun and StatusContext shapes appear; normalize.
            name = c.get("name") or c.get("context") or "?"
            bucket = (c.get("bucket")
                      or (c.get("conclusion") or "").lower()
                      or (c.get("state") or "").lower())
            checks.append({"name": name, "bucket": bucket})
        out.append({
            "n": pr["number"], "s": pr["state"],
            "m": pr.get("mergeStateStatus", "UNKNOWN"),
            "t": pr["title"][:60], "checks": checks,
        })
    return sorted(out, key=lambda x: -x["n"])

def diff_emit(state: dict, cur: list) -> tuple[list[str], dict, set]:
    """Compare prev vs cur, build event list.

    Returns (events, new_pending_seen, new_no_checks_active).

    v5 (2026-05-19): explicit no-checks-active set tracking. The prior
    condition-chain implementation could re-emit NO-CHECKS for the same PR
    when the brief presence of a check (then absence) flipped a sub-condition.
    The set-based approach is monotonic: a PR fires NO-CHECKS exactly once
    per transition INTO the no-checks state. Seed-stuck PRs are absorbed
    into the set at startup (see main()) so they never emit (they were
    already broken before monitor came up; no point spamming).
    """
    events: list[str] = []
    prev_prs = {p["n"]: p for p in state.get("prs", [])}
    pending_seen: dict[str, str] = state.get("pending_seen", {})
    new_pending_seen: dict[str, str] = {}
    stalled_marked: set = set(state.get("stalled_emitted", []))
    no_checks_active: set = set(state.get("no_checks_active", []))
    new_no_checks_active: set = set()

    for c in cur:
        n, title = c["n"], c["t"]
        prev = prev_prs.get(n)

        # PR-level
        if prev is None:
            events.append(f"NEW PR #{n} state={c['s']} mergeStatus={c['m']} | {title}")
        else:
            if c["s"] != prev["s"]:
                if c["s"] == "MERGED":   events.append(f"MERGED #{n} | {title}")
                elif c["s"] == "CLOSED": events.append(f"CLOSED #{n} (unmerged) | {title}")
                else:                    events.append(f"state #{n} {prev['s']} -> {c['s']} | {title}")
            if c["m"] != prev["m"]:
                if c["m"] in ("BEHIND", "DIRTY"):
                    events.append(f"{c['m']} #{n} (needs rebase) | {title}")
                elif c["m"] == "CLEAN":
                    events.append(f"CLEAN #{n} (CI green, ready to merge) | {title}")
                elif c["m"] == "UNSTABLE":
                    events.append(f"UNSTABLE #{n} (mergeable, non-required failing) | {title}")

        # NO-CHECKS detection: an OPEN PR that is BLOCKED with zero checks at
        # all is the canonical "stuck after rebase dropped the old head's CI"
        # pattern. GitHub will never satisfy auto-merge because the required
        # checks don't exist. The fix is `scripts/pr-unstick.sh <PR>` to
        # close+reopen and retrigger checks. Emit ONCE per transition INTO
        # this state; a PR that stays NO-CHECKS does not re-emit.
        in_no_checks = (c["s"] == "OPEN" and c["m"] == "BLOCKED" and len(c["checks"]) == 0)
        if in_no_checks:
            new_no_checks_active.add(n)
            if n not in no_checks_active:
                events.append(f"NO-CHECKS #{n} (open + BLOCKED + zero checks; run scripts/pr-unstick.sh {n}) | {title}")
        # else: PR no longer in NO-CHECKS state; not added to new_no_checks_active

        # Check-level: failures, recoveries, stalls
        prev_checks = {x["name"]: x["bucket"] for x in (prev["checks"] if prev else [])}
        for chk in c["checks"]:
            name, bucket = chk["name"], chk["bucket"]
            prev_bucket = prev_checks.get(name)
            if bucket == "fail" and prev_bucket != "fail":
                events.append(f"CI-FAIL #{n}: {name} | {title}")
            elif bucket == "pass" and prev_bucket == "fail":
                events.append(f"CI-RECOVERED #{n}: {name} | {title}")

            if bucket == "pending":
                key = f"{n}:{name}"
                first_seen = pending_seen.get(key, now_iso())
                new_pending_seen[key] = first_seen
                if key not in stalled_marked:
                    elapsed_min = (datetime.now(timezone.utc) - datetime.fromisoformat(first_seen)).total_seconds() / 60
                    if elapsed_min > STALL_MIN:
                        events.append(
                            f"STALLED #{n}: {name} pending for {elapsed_min:.1f}min | {title}"
                        )
                        stalled_marked.add(key)
            else:
                # Cleared (not pending anymore); drop any stall marker
                stalled_marked.discard(f"{n}:{name}")

    state["stalled_emitted"] = sorted(stalled_marked)
    state["no_checks_active"] = sorted(new_no_checks_active)
    return events, new_pending_seen, new_no_checks_active

def main():
    emit(f"[start] repo={REPO} interval={INTERVAL}s state={STATE} "
         f"lastpoll={LASTPOLL} stall_threshold={STALL_MIN}min heartbeat_every={HEARTBEAT_POLLS}polls quiet={QUIET}")

    seed = snapshot()
    if seed is None:
        emit("[fatal] initial snapshot failed; aborting")
        sys.exit(1)

    # Seed-time NO-CHECKS absorption (v5): mark any PR that's already in the
    # NO-CHECKS state at startup as "already known broken." This prevents the
    # monitor from spamming NO-CHECKS events for PRs that were stuck before
    # the monitor came up; the operator presumably already knew about them or
    # the previous monitor invocation already flagged them.
    seed_no_checks = sorted({
        p["n"] for p in seed
        if p["s"] == "OPEN" and p["m"] == "BLOCKED" and len(p["checks"]) == 0
    })

    state = {
        "prs": seed,
        "pending_seen": {},
        "stalled_emitted": [],
        "no_checks_active": seed_no_checks,
        "poll": 0,
    }
    STATE.write_text(json.dumps(state))
    LASTPOLL.write_text(now_iso())
    if seed_no_checks:
        emit(f"[seed] {len(seed)} PRs in baseline; absorbing {len(seed_no_checks)} "
             f"seed-stuck PR(s) (NO-CHECKS pre-existing): {seed_no_checks}; emitting changes only")
    else:
        emit(f"[seed] {len(seed)} PRs in baseline; emitting changes only")

    while True:
        time.sleep(INTERVAL)
        state["poll"] += 1

        cur = snapshot()
        if cur is None:
            emit(f"[poll {state['poll']} | error] gh pr list returned empty / failed; will retry")
            LASTPOLL.write_text(now_iso() + " (last poll: snapshot failed)")
            continue

        events, new_pending, _new_no_checks = diff_emit(state, cur)
        LASTPOLL.write_text(now_iso())

        if events:
            emit(f"[poll {state['poll']} | {len(events)} event(s)]")
            for line in events:
                print(f"  {now_hms()} {line}", flush=True)
        elif (not QUIET) and (state["poll"] % HEARTBEAT_POLLS == 0):
            n_open = sum(1 for p in cur if p["s"] == "OPEN")
            n_pending = sum(1 for p in cur for c in p["checks"] if c["bucket"] == "pending")
            n_stalled = len(state.get("stalled_emitted", []))
            n_no_checks = len(state.get("no_checks_active", []))
            emit(f"[heartbeat poll {state['poll']} | {n_open} open PR(s), "
                 f"{n_pending} pending check(s), {n_stalled} stalled marker(s), "
                 f"{n_no_checks} no-checks marker(s); no changes]")

        state["prs"] = cur
        state["pending_seen"] = new_pending
        STATE.write_text(json.dumps(state))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit("[stop] keyboard interrupt")
    except Exception as e:
        emit(f"[fatal] uncaught: {type(e).__name__}: {e}")
        raise
