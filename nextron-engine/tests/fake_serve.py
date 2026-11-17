"""A stdlib fake of ``nextron serve`` for the ``scripts/run.ps1`` tests (Story 1.21).

It imports nothing from ``nextron`` and opens no Nextron data dir. It touches a heartbeat file the
way the engine's heartbeat thread does, and its command line says how it behaves:

- ``--beat-delay`` (wait before the first beat), ``--stall`` (beat once, then never again),
  ``--exit-after`` with ``--exit-code`` (exit by itself), ``--grandchild`` (start a sleeping child
  Python, so a kill must take a whole tree);
- ``--on-break graceful`` (the default) answers CTRL_BREAK as the engine does: it logs ``break``,
  waits ``--break-delay`` seconds, then exits 0. ``--on-break ignore`` logs ``break`` and carries
  on, so the supervisor's stop grace runs out.

It writes JSON lines to ``--log``: ``start`` (``pid``, ``ppid``, ``nextron_env``,
``nextron_data_dir`` and, with ``--grandchild``, ``grandchild_pids``), ``break`` and ``exit``
(``code``). A killed fake logs no ``exit``.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

TICK_S = 0.05
GRANDCHILD_LIFETIME_S = 600  # a leaked grandchild still ends on its own
GRANDCHILD_CODE = (
    "import os, sys, time\n"
    "sys.stdout.write(f'{os.getpid()}\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdout.close()\n"
    f"time.sleep({GRANDCHILD_LIFETIME_S})\n"
)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--heartbeat", type=Path, required=True, help="the file to touch")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between beats")
    parser.add_argument("--log", type=Path, required=True, help="the JSON-line log")
    parser.add_argument("--beat-delay", type=float, default=0.0, help="seconds to the 1st beat")
    parser.add_argument("--stall", action="store_true", help="beat once, then never again")
    parser.add_argument("--exit-after", type=float, help="exit by itself after these seconds")
    parser.add_argument("--exit-code", type=int, default=0, help="the code --exit-after uses")
    parser.add_argument("--grandchild", action="store_true", help="start a sleeping child")
    parser.add_argument(
        "--on-break", choices=("graceful", "ignore"), default="graceful", help="CTRL_BREAK"
    )
    parser.add_argument("--break-delay", type=float, default=0.0, help="seconds to stop")
    return parser.parse_args(argv)


def log(path: Path, event: str, **fields: object) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    line = json.dumps({"ts": stamp, "event": event, **fields}, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def start_grandchild() -> tuple[subprocess.Popen[str], list[int]]:
    """A sleeping child Python, and every pid it runs as: the process this one started and,
    behind a venv launcher, the interpreter that reports its own pid."""
    child = subprocess.Popen(
        [sys.executable, "-c", GRANDCHILD_CODE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert child.stdout is not None
    reported = child.stdout.readline().strip()
    child.stdout.close()
    pids = [child.pid]
    if reported.isdigit() and int(reported) != child.pid:
        pids.append(int(reported))
    return child, pids


def stop_grandchild(child: subprocess.Popen[str]) -> None:
    if child.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.heartbeat.parent.mkdir(parents=True, exist_ok=True)
    breaks: list[int] = []

    def on_break(signum: int, frame: FrameType | None) -> None:
        breaks.append(signum)

    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_break)
    started = time.monotonic()
    grandchild: subprocess.Popen[str] | None = None
    fields: dict[str, object] = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "nextron_env": os.environ.get("NEXTRON_ENV"),
        "nextron_data_dir": os.environ.get("NEXTRON_DATA_DIR"),
    }
    try:
        if args.grandchild:
            grandchild, fields["grandchild_pids"] = start_grandchild()
        log(args.log, "start", **fields)
        next_beat = started + args.beat_delay
        stalled = False
        logged_breaks = 0
        while True:
            now = time.monotonic()
            if len(breaks) > logged_breaks:
                logged_breaks = len(breaks)
                log(args.log, "break", on_break=args.on_break)
                if args.on_break == "graceful":
                    time.sleep(args.break_delay)
                    log(args.log, "exit", code=0)
                    return 0
            if args.exit_after is not None and now - started >= args.exit_after:
                log(args.log, "exit", code=args.exit_code)
                return args.exit_code
            if not stalled and now >= next_beat:
                args.heartbeat.touch()  # creates it, or sets its modification time to now
                next_beat = now + args.interval
                stalled = args.stall
            time.sleep(TICK_S)
    finally:
        if grandchild is not None:
            stop_grandchild(grandchild)


if __name__ == "__main__":
    sys.exit(main())
