"""The ``nextron`` command and ``python -m nextron`` (composition root CLI).

Commands:

- ``serve [--reload]``: run the engine for ``NEXTRON_ENV`` (exit 0: stopped normally; exit 1:
  unexpected error; exit 2: invalid config, a failed startup check (a database ahead of the
  code or not this engine's included), an unusable data dir, a failed database migration or a
  server that failed to start; exit 3: another instance holds the lock). ``--reload`` is for
  ``dev`` only.
- ``doctor [--env {dev,test,prod}] [--json]``: check that this install is ready, and print each
  check's status (``ok``, ``warn`` or ``fail``) and message as a table, or as JSON with
  ``--json``. ``--env`` checks that environment instead of ``NEXTRON_ENV`` (exit 0 when nothing
  fails, warnings allowed; 1 when a check fails; 2 when ``NEXTRON_ENV`` or another bootstrap
  setting is invalid, or the arguments are).
- ``open``: open the dashboard in the default browser through a one-time link that works once,
  within 5 minutes (exit 0: opened; exit 1: the engine isn't running, or the code couldn't be
  stored or the browser opened; exit 2: ``NEXTRON_ENV`` or another bootstrap setting is
  invalid).
- ``seed sync [--env {dev,test,prod}]``: load ``seed/segments.yaml`` and ``seed/markets.yaml``
  into the database in one transaction: create each new Segment and Market, give each changed
  one a new version, and leave a stored one the file no longer lists as it is; print
  ``segments: <n> changed, <m> unchanged`` and a line per Segment created, changed or not in the
  file, then ``markets: <n> changed, <m> unchanged`` and a line per Market. ``--env`` syncs that
  environment instead of ``NEXTRON_ENV``. The database must exist and be migrated: start the
  engine once first (exit 0: synced; exit 1: a file is missing, unreadable or invalid (the
  message names the file and the line or field), the database is missing, behind, ahead of the
  code ("newer than this code") or not this engine's, or the sync failed, and nothing was
  changed; exit 2: ``NEXTRON_ENV`` or another bootstrap setting is invalid, or the arguments
  are).

A later story adds ``restore``.
"""

import argparse
import sys
from collections.abc import Sequence

from nextron import main as engine
from nextron.bootstrap import ENVIRONMENTS


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
    doctor = commands.add_parser(
        "doctor", help="check that this install is ready to run, and print each check"
    )
    doctor.add_argument(
        "--env",
        choices=ENVIRONMENTS,
        help="the environment to check, instead of NEXTRON_ENV (for this run only)",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help='print the results as JSON: [{"id", "status", "message"}, ...]',
    )
    commands.add_parser(
        "open", help="open the dashboard in the default browser with a one-time link"
    )
    seed = commands.add_parser("seed", help="load the seed files into the database")
    seed_commands = seed.add_subparsers(dest="seed_command", required=True, metavar="subcommand")
    sync = seed_commands.add_parser(
        "sync",
        help=(
            "create or update the Segments and Markets from seed/segments.yaml and "
            "seed/markets.yaml (never re-scores a Lead)"
        ),
    )
    sync.add_argument(
        "--env",
        choices=ENVIRONMENTS,
        help="the environment to sync, instead of NEXTRON_ENV (for this run only)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return engine.serve(reload=args.reload)
    if args.command == "open":
        return engine.open_dashboard()
    if args.command == "seed" and args.seed_command == "sync":
        return engine.seed_sync(env=args.env)
    if args.command == "doctor":
        return engine.doctor(env=args.env, as_json=args.as_json)
    raise AssertionError(f"the parser accepted a command main doesn't dispatch: {args}")


if __name__ == "__main__":
    sys.exit(main())
