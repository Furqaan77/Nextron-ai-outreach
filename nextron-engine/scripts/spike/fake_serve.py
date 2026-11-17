"""A fake ``nextron serve`` for the OQ-A9 spike (Story 1.20). Throwaway: Story 1.21 replaces it.

Stdlib only; it imports nothing from ``nextron`` and opens no Nextron data dir. It touches a
heartbeat file the way the engine's heartbeat thread does, and can be told to stall or exit:

- persistent behaviours come from the command line (the tests use these):
  ``--beat-delay`` (wait before the first beat), ``--stall`` (beat once, then never again),
  ``--exit-after`` with ``--exit-code`` (exit by itself), ``--grandchild`` (start a sleeping child
  Python, so a kill must take a whole tree);
- one-shot commands come from the ``--control`` file (``oq_a9.ps1 -Action Stall`` or ``Exit3``
  writes it): ``stall`` stops the beats, ``exit <n>`` exits with ``n``. The child that reads a
  command claims the file by renaming it, then deletes it, so each command is obeyed once.

It writes JSON lines to ``--log``: ``start`` (``pid``, ``ppid``, ``nextron_env`` and, with
``--grandchild``, ``grandchild_pids``), ``command`` (``text``, ``accepted``) and ``exit``
(``code``). A killed fake logs no ``exit``.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--heartbeat", type=Path, required=True, help="the file to touch")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between beats")
    parser.add_argument("--control", type=Path, help="the one-shot command file")
    parser.add_argument("--log", type=Path, required=True, help="the JSON-line log")
    parser.add_argument("--beat-delay", type=float, default=0.0, help="seconds to the 1st beat")
    parser.add_argument("--stall", action="store_true", help="beat once, then never again")
    parser.add_argument("--exit-after", type=float, help="exit by itself after these seconds")
    parser.add_argument("--exit-code", type=int, default=0, help="the code --exit-after uses")
    parser.add_argument("--grandchild", action="store_true", help="start a sleeping child")
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


def read_command(control: Path) -> str | None:
    """The pending one-shot command, claimed by this process, or ``None``."""
    claimed = control.with_name(f"{control.name}.{os.getpid()}.claimed")
    try:
        os.replace(control, claimed)
    except OSError:
        return None
    try:
        return claimed.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):  # unreadable, or not UTF-8 (say, UTF-16): ignored
        return ""
    finally:
        claimed.unlink(missing_ok=True)


def parse_command(text: str) -> tuple[str, int | None] | None:
    parts = text.split()
    if parts == ["stall"]:
        return "stall", None
    code = parts[1] if len(parts) == 2 and parts[0] == "exit" else ""
    if code.isascii() and code.isdigit() and int(code) <= 255:  # "²" is a digit, not ASCII
        return "exit", int(code)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.heartbeat.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    grandchild: subprocess.Popen[str] | None = None
    fields: dict[str, object] = {
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "nextron_env": os.environ.get("NEXTRON_ENV"),
    }
    try:
        if args.grandchild:
            grandchild, fields["grandchild_pids"] = start_grandchild()
        log(args.log, "start", **fields)
        next_beat = started + args.beat_delay
        stalled = False
        while True:
            now = time.monotonic()
            if args.control is not None:
                text = read_command(args.control)
                if text is not None:
                    command = parse_command(text)
                    log(args.log, "command", text=text, accepted=command is not None)
                    if command == ("stall", None):
                        stalled = True
                    elif command is not None and command[1] is not None:
                        log(args.log, "exit", code=command[1])
                        return command[1]
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
