"""Runs setup automatically instead of requiring a student to run scripts
by hand — the real fix for "this is a script, not automation": in
production, model registration/promotion/cataloging are not things an
engineer SSHs in and runs manually every time, they're steps a service
performs on its own. This module runs the exact same, already-tested
scripts (model-governance/register_model.py, promote_model.py,
data-governance/ingest_and_tag_pii.py) as subprocesses — no logic
duplicated, no behavior diverges between "run it yourself" and "it runs
itself" — just automatic triggering instead of manual.

Two mechanisms:
  - `run_initial_setup()`: register + promote, run synchronously on agent
    startup (blocking — /chat can't work without a Production model, so
    there's no point serving traffic before this finishes).
  - `start_background_scheduler()`: a daemon thread that (a) catalogs/tags
    PII once, then (b) re-runs the promotion gate on a fixed interval —
    real, continuous re-evaluation with a real consequence (demotion, see
    promote_model.py) if a model's measured quality degrades, not a
    one-time check that's never revisited.
"""
import os
import subprocess
import sys
import threading
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RE_EVAL_INTERVAL_SECONDS = int(os.environ.get("RE_EVAL_INTERVAL_SECONDS", str(6 * 3600)))


def _run(script_rel_path: str) -> bool:
    result = subprocess.run(
        [sys.executable, script_rel_path], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300
    )
    print(f"[startup_automation] {script_rel_path} exit={result.returncode}")
    for line in (result.stdout or "").splitlines()[-20:]:
        print(f"  {line}")
    if result.returncode != 0:
        for line in (result.stderr or "").splitlines()[-20:]:
            print(f"  ERR: {line}")
    return result.returncode == 0


def run_initial_setup() -> None:
    """Idempotent — safe to run on every container start (register_model.py
    skips versions it's already registered; promote_model.py only changes
    stage when the gate's verdict actually differs from the current one)."""
    _run(os.path.join("model-governance", "register_model.py"))
    _run(os.path.join("model-governance", "promote_model.py"))


def _background_loop() -> None:
    ingest_path = os.path.join("data-governance", "ingest_and_tag_pii.py")
    for attempt in range(30):
        if _run(ingest_path):
            break
        print(f"[startup_automation] ingest_and_tag_pii.py failed (attempt {attempt + 1}/30), retrying in 10s")
        time.sleep(10)
    while True:
        time.sleep(RE_EVAL_INTERVAL_SECONDS)
        print("[startup_automation] scheduled re-evaluation starting")
        _run(os.path.join("model-governance", "promote_model.py"))


def start_background_scheduler() -> None:
    threading.Thread(target=_background_loop, daemon=True, name="governance-scheduler").start()
