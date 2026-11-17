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
- ``restore <file> [--yes]``: put the backup copy ``file`` (``nextron-<env>-<stamp>.db``, or
  ``.db.enc`` encrypted with the Backup passphrase) back as this environment's database, with
  the engine stopped, and turn recovery mode on, which holds sending until every recovery step
  is done. The current database is saved to ``<backup dir>\\pre_restore\\`` first. Without
  ``--yes`` it asks you to type ``yes`` (exit 0: restored; exit 1: refused, cancelled or failed,
  the engine running included; exit 2: ``NEXTRON_ENV`` or another bootstrap setting is invalid,
  or an environment check failed).

A later story adds ``seed sync``.
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
    restore = commands.add_parser(
        "restore",
        help="restore a backup copy with the engine stopped, and hold sending in recovery mode",
    )
    restore.add_argument(
        "file", help="the backup copy: nextron-<env>-<stamp>.db, or .db.enc (encrypted)"
    )
    restore.add_argument(
        "--yes", action="store_true", help="restore without asking you to type yes"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return engine.serve(reload=args.reload)
    if args.command == "open":
        return engine.open_dashboard()
    if args.command == "restore":
        return engine.restore(args.file, yes=args.yes)
    return engine.doctor(env=args.env, as_json=args.as_json)


if __name__ == "__main__":
    sys.exit(main())
