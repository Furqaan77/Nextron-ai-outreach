"""The network guard of every engine a test starts (AD-14, Story 4.5, decision 10).

``tests.live_guard.child_env(variables)`` puts this folder, then the engine root, first on the
child's ``PYTHONPATH``, so Python's ``site`` imports this module before ``python -m nextron``
runs. It installs ``tests.live_guard.guard_network`` (no allowed host) on a ``MonkeyPatch`` that
is never undone: the child can look up and connect to loopback only, so a test-started engine
can never reach the Claude API, or any other service, whatever credentials it finds.

A guard that can't be installed fails closed: Python would only print a sitecustomize error and
go on unguarded, so this module ends the process with exit code ``GUARD_FAILED`` (97) instead.
"""

import os
import sys

GUARD_FAILED = 97

try:
    import pytest

    from tests.live_guard import guard_network

    guard_network(pytest.MonkeyPatch())
except BaseException as error:  # noqa: BLE001 - any failure must stop the child unguarded
    sys.stderr.write(f"the child network guard failed: {type(error).__name__}: {error}\n")
    sys.stderr.flush()
    os._exit(GUARD_FAILED)
