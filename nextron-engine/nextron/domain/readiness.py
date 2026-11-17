"""Readiness check results (FR-56, FR-58). Standard library only.

``nextron doctor`` and the health panel show the same list of checks. Each check gives a
``CheckResult(id, status, message)``: ``status`` is a ``Status`` (``ok``, ``warn`` or ``fail``)
and ``message`` one line, safe to show. ``nextron.startup_checks`` runs the checks (and
re-exports both types); the ``ops`` service and the dashboard only read results, so the types
live here, below them.

``tally(results)`` is ``(ok, warn, fail)``: how many results have each status.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    """A check's outcome: ``ok``, ``warn`` (needs attention, doesn't stop the engine) or
    ``fail``."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check; ``message`` explains it in one line."""

    id: str
    status: Status
    message: str


def tally(results: Iterable[CheckResult]) -> tuple[int, int, int]:
    """How many of ``results`` are ``ok``, ``warn`` and ``fail``, in that order."""
    ok = warn = fail = 0
    for result in results:
        if result.status is Status.OK:
            ok += 1
        elif result.status is Status.WARN:
            warn += 1
        elif result.status is Status.FAIL:
            fail += 1
    return ok, warn, fail
