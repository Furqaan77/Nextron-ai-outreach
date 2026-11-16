"""The ``nextron`` command and ``python -m nextron`` (composition root CLI).

Commands:

- ``serve [--reload]``: run the engine for ``NEXTRON_ENV`` (exit 0: stopped normally; exit 1:
  unexpected error; exit 2: invalid config, a failed startup check (a database ahead of the
  code or not this engine's included), an unusable data dir, a failed database migration or a
  server that failed to start; exit 3: another instance holds the lock). ``--reload`` is for
  ``dev`` only.
- ``doctor``: print each startup check as PASS or FAIL (exit 0 when none fail, 1 when any
  fails, 2 when ``NEXTRON_ENV`` or another bootstrap setting is invalid).

Later stories add ``open``, ``seed sync`` and ``restore``.
"""

import argparse
import sys
from collections.abc import Sequence

from nextron import main as engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nextron",
        description="Nextron Outreach Engine. NEXTRON_ENV must be set to dev, test or prod.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    serve = commands.add_parser("serve", help="run the engine and dashboard on 127.0.0.1")
    serve.add_argument(
        "--reload",
        action="store_true",
        help="restart when the code changes (NEXTRON_ENV=dev only)",
    )
    commands.add_parser("doctor", help="print each startup check as PASS or FAIL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return engine.serve(reload=args.reload)
    return engine.doctor()


if __name__ == "__main__":
    sys.exit(main())
