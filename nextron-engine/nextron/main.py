"""Composition root (AD-1, AD-2, AD-14): wiring, startup checks, the instance lock and Uvicorn.

``serve`` runs these steps in order and stops at the first failure. The supervisor
(``scripts/run.ps1``, Story 1.21) relies on the exit codes:

1. Bootstrap config, including ``NEXTRON_ENV``: exit 2, before any file or folder exists.
2. Environment checks (SQLite version, tzdata, OneDrive): exit 2, still before any folder exists.
3. Create the data dir and its ``logs`` folder, then take ``<data dir>\\nextron.lock``: exit 3
   when another instance of the same environment holds it, exit 2 when the folders or the lock
   file can't be created.
4. The port check: exit 2. It runs after the lock because a second instance of the same
   environment finds its port taken by the first; it must report exit 3, not a busy port.
5. Uvicorn on 127.0.0.1 with one worker (``--reload`` only in ``dev``) until it stops: exit 0.
   Uvicorn exits with code 3 when it can't bind or start the app; ``serve`` reports that as
   exit 2, so it never looks like a held lock.

Any other error ends with a traceback and exit 1.

``doctor`` runs every check, prints PASS or FAIL for each, and exits 0 when none fail, 1 when
any fails, and 2 when ``NEXTRON_ENV`` or another bootstrap setting is invalid. It takes no lock,
creates nothing and starts no server.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final, TextIO

import uvicorn
from fastapi import FastAPI

from nextron import __version__
from nextron.bootstrap import BootstrapConfig, BootstrapError, load_bootstrap
from nextron.lock import LOCK_FILE_NAME, InstanceLock, LockHeldError
from nextron.startup_checks import (
    CheckResult,
    Probes,
    all_checks,
    check_port_free,
    environment_checks,
)
from nextron.web.app import create_app

HOST: Final = "127.0.0.1"
WORKERS: Final = 1
LOGS_DIR_NAME: Final = "logs"
PACKAGE_DIR: Final = Path(__file__).resolve().parent

EXIT_OK: Final = 0
EXIT_CHECKS_FAILED: Final = 1  # doctor: at least one check failed
EXIT_STARTUP_FAILED: Final = 2  # invalid config or a failed startup check
EXIT_LOCK_HELD: Final = 3  # another instance runs for this environment

ServerRunner = Callable[[BootstrapConfig, bool], None]


def build_app(config: BootstrapConfig) -> FastAPI:
    return create_app(env=config.env, version=__version__)


def create_app_from_environment() -> FastAPI:
    """App factory for Uvicorn's reload worker, which starts in a fresh process."""
    return build_app(load_bootstrap())


def run_uvicorn(config: BootstrapConfig, reload: bool) -> None:
    """Serve on 127.0.0.1 only, with one worker, until Uvicorn stops."""
    if reload:
        uvicorn.run(
            "nextron.main:create_app_from_environment",
            factory=True,
            reload=True,
            reload_dirs=[str(PACKAGE_DIR)],
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
        )
    else:
        uvicorn.run(
            build_app(config),
            host=HOST,
            port=config.port,
            workers=WORKERS,
            proxy_headers=False,
        )


def _report_failures(results: list[CheckResult], err: TextIO) -> bool:
    failures = [result for result in results if not result.ok]
    for result in failures:
        print(f"startup check failed: {result.name}: {result.detail}", file=err)
    return bool(failures)


def serve(
    *,
    reload: bool = False,
    probes: Probes | None = None,
    run_server: ServerRunner = run_uvicorn,
    err: TextIO | None = None,
) -> int:
    """Start the engine for ``NEXTRON_ENV``; return the process exit code."""
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    if reload and config.env != "dev":
        print("--reload is allowed only when NEXTRON_ENV=dev", file=err)
        return EXIT_STARTUP_FAILED

    probes = probes if probes is not None else Probes.system()
    if _report_failures(environment_checks(config.data_dir, probes), err):
        return EXIT_STARTUP_FAILED

    lock = InstanceLock(config.data_dir / LOCK_FILE_NAME)
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        (config.data_dir / LOGS_DIR_NAME).mkdir(exist_ok=True)
        lock.acquire()
    except LockHeldError:
        print(f"another Nextron instance is running for {config.env}", file=err)
        return EXIT_LOCK_HELD
    except OSError as exc:
        print(f"cannot prepare {exc.filename or config.data_dir}: {exc.strerror or exc}", file=err)
        return EXIT_STARTUP_FAILED
    try:
        if _report_failures([check_port_free(HOST, config.port, probes)], err):
            return EXIT_STARTUP_FAILED
        try:
            run_server(config, reload)
        except SystemExit as exc:
            if exc.code in (None, 0):
                return EXIT_OK
            print(f"server failed to start (Uvicorn exit code {exc.code})", file=err)
            return EXIT_STARTUP_FAILED
    finally:
        lock.release()
    return EXIT_OK


def doctor(
    *,
    probes: Probes | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print every startup check as PASS or FAIL; return 0 when none failed, else 1."""
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        config = load_bootstrap()
    except BootstrapError as exc:
        print(exc, file=err)
        return EXIT_STARTUP_FAILED
    probes = probes if probes is not None else Probes.system()
    results = all_checks(config.data_dir, HOST, config.port, probes)
    width = max(len(result.name) for result in results)
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status}  {result.name:<{width}}  {result.detail}", file=out)
    return EXIT_OK if all(result.ok for result in results) else EXIT_CHECKS_FAILED
