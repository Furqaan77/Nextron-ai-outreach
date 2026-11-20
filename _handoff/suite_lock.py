"""Run the full engine suite (pytest + lint-imports) under a machine-wide lock.

Two build tracks share this PC, and the suite's serve-process tests bind the fixed
test port 8767, so only one full suite may run at a time.

Usage:  python suite_lock.py <nextron-engine dir> [label] [-- extra pytest args]
Exit code: 0 when pytest and lint-imports both pass, else 1 (2 on lock timeout).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LOCK = Path(__file__).with_name("pytest-suite.lock")
TMP_ROOT = Path(r"e:\Nextron AI outreach\_handoff\scratch\tmp")
STALE_S = 4 * 3600  # backstop only; a live holder is never cleared
WAIT_S = 6 * 3600  # 4 tracks + a merge can queue for hours


def _pid_alive(pid) -> bool:
    """True if a process with this id is still running (Windows)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def _owner():
    try:
        return json.loads((LOCK / "owner.json").read_text())
    except Exception:
        return None


def acquire(label: str) -> None:
    deadline = time.time() + WAIT_S
    announced = False
    while True:
        try:
            os.mkdir(LOCK)
            (LOCK / "owner.json").write_text(
                json.dumps({"pid": os.getpid(), "label": label, "at": time.time()})
            )
            return
        except FileExistsError:
            info = _owner()
            if info is None:
                # Holder may not have written owner.json yet: give it a minute.
                try:
                    age = time.time() - LOCK.stat().st_mtime
                except OSError:
                    continue
                stale = age > 60
                holder = "?"
            else:
                age = time.time() - info.get("at", 0)
                holder = info.get("label")
                # Only a dead holder is stale; the age limit is a backstop for pid reuse.
                stale = (not _pid_alive(info.get("pid"))) or age > STALE_S
            if stale:
                # Re-check just before clearing so two waiters can't both take over.
                if _owner() == info:
                    shutil.rmtree(LOCK, ignore_errors=True)
                time.sleep(1)
                continue
            if not announced:
                print(f"waiting for suite lock held by {holder!r} ({int(age)} s)", flush=True)
                announced = True
            if time.time() > deadline:
                print("gave up waiting for the suite lock", flush=True)
                sys.exit(2)
            time.sleep(10)


def main() -> int:
    engine = Path(sys.argv[1]).resolve()
    rest = sys.argv[2:]
    label = rest[0] if rest and rest[0] != "--" else str(engine)
    extra = rest[rest.index("--") + 1 :] if "--" in rest else []
    # Parallel once the tree has pytest-xdist (chore aee9466); serial otherwise.
    pyproject = (engine / "pyproject.toml").read_text(encoding="utf-8")
    if "pytest-xdist" in pyproject and not any(a == "-n" or a.startswith("-n") for a in extra):
        extra = ["-n", "4", "--dist", "loadgroup", *extra]
    # C: is nearly full: keep pytest's temp tree on E: (one folder per label).
    if not any(a.startswith("--basetemp") for a in extra):
        base = TMP_ROOT / re.sub(r"[^A-Za-z0-9_.-]", "_", label)[-60:]
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        extra = [*extra, f"--basetemp={base}"]
    acquire(label)
    try:
        env = dict(os.environ, NEXTRON_ENV="test")
        t = time.time()
        py = subprocess.run(["uv", "run", "pytest", "-q", *extra], cwd=engine, env=env)
        print(f"[suite_lock] pytest exit {py.returncode} in {int(time.time() - t)} s", flush=True)
        li = subprocess.run(["uv", "run", "lint-imports"], cwd=engine, env=env,
                            capture_output=True, text=True)
        print("\n".join(li.stdout.splitlines()[-3:]), flush=True)
        print(f"[suite_lock] lint-imports exit {li.returncode}", flush=True)
        return 0 if py.returncode == 0 and li.returncode == 0 else 1
    finally:
        shutil.rmtree(LOCK, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
