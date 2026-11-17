"""Engine health (AD-10, AD-20, FR-58, OP-9): the worker rule, health issues and their
registry, and each job group's last successful runs. Standard library only.

**The worker.** ``HEARTBEAT_FRESH`` is 2 minutes. ``worker_running(beat_at, now)`` is whether
the ``runner_heartbeat`` row's ``beat_at`` is less than that from ``now``, in either direction:
a beat 2 minutes or more in the future (the clock was set back) isn't fresh either.
``nextron doctor``'s ``port_free`` check uses the same threshold
(``nextron.startup_checks.HEARTBEAT_FRESH_S``).

**Health issues.** ``HealthIssue(kind, subject, text, detail=None)`` is one error needing the
Operator's attention, checked when it is built: ``kind`` fullmatches ``[a-z][a-z0-9_]{0,31}``;
``subject`` is a ULID or fullmatches ``[a-z0-9_:.-]{1,64}``; ``text`` is a non-blank ``str``;
``detail`` is ``None`` or a ``str``. Anything else raises ``TypeError`` or ``ValueError``,
naming the field, never its value.

**The registry.** ``IssueSource(name, read)`` is one owner's source of issues: ``name``
fullmatches ``[a-z][a-z0-9_]{0,31}`` and ``read()`` returns an iterable of ``HealthIssue``.
``HealthIssues`` holds the sources: ``register_source(source)`` (``TypeError`` for anything but
an ``IssueSource``, ``ValueError`` for a name already registered, ``RuntimeError`` once
frozen), ``freeze()``, ``frozen`` and ``sources`` (a tuple in registration order). Only
``nextron.main.build_health_issues`` constructs one or calls ``register_source``
(``tests/contracts/test_health_contracts.py``); each owner adds its source there.

**Last successful runs.** ``job_group_runs(job_types, last)`` takes the registered job types
as ``(name, group)`` pairs in registration order and ``last``, each type's last success by
name, and returns one ``GroupRuns(group, runs)`` per ``JobGroup`` that has a type, in
``JobGroup`` order; ``runs`` holds a ``LastRun(job_type, succeeded_at)`` per type, in
registration order, with ``succeeded_at`` ``None`` when the type has never succeeded.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from nextron.domain.ids import is_ulid
from nextron.domain.job import JobGroup

HEARTBEAT_FRESH: Final = timedelta(minutes=2)  # the panel's "running" threshold (AD-20)

KIND_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")  # issue kinds and source names
SUBJECT_PATTERN: Final = re.compile(r"[a-z0-9_:.-]{1,64}")


def worker_running(beat_at: datetime, now: datetime) -> bool:
    """Whether a heartbeat at ``beat_at`` is fresh at ``now``: less than ``HEARTBEAT_FRESH``
    away, in either direction."""
    return abs(now - beat_at) < HEARTBEAT_FRESH


# Health issues ------------------------------------------------------------------------------


def _name(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a str")
    if KIND_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must match {KIND_PATTERN.pattern}")


@dataclass(frozen=True, slots=True)
class HealthIssue:
    """One error needing attention; see the module docstring."""

    kind: str
    subject: str
    text: str
    detail: str | None = None

    def __post_init__(self) -> None:
        _name("kind", self.kind)
        if not isinstance(self.subject, str):
            raise TypeError("subject must be a str")
        if not (is_ulid(self.subject) or SUBJECT_PATTERN.fullmatch(self.subject)):
            raise ValueError(f"subject must be a ULID or match {SUBJECT_PATTERN.pattern}")
        if not isinstance(self.text, str):
            raise TypeError("text must be a str")
        if not self.text.strip():
            raise ValueError("text must not be blank")
        if self.detail is not None and not isinstance(self.detail, str):
            raise TypeError("detail must be None or a str")


@dataclass(frozen=True, slots=True)
class IssueSource:
    """One owner's source of health issues: ``read()`` returns them."""

    name: str
    read: Callable[[], Iterable[HealthIssue]]

    def __post_init__(self) -> None:
        _name("name", self.name)
        if not callable(self.read):
            raise TypeError(f"issue source {self.name}: read must be callable")


class HealthIssues:
    """The health-issue registry; see the module docstring."""

    def __init__(self) -> None:
        self._sources: list[IssueSource] = []
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def sources(self) -> tuple[IssueSource, ...]:
        """Every registered source, in registration order."""
        return tuple(self._sources)

    def register_source(self, source: IssueSource) -> None:
        """Add ``source``; raises ``RuntimeError`` once frozen, ``TypeError`` for anything but
        an ``IssueSource`` and ``ValueError`` for a name already registered."""
        if self._frozen:
            raise RuntimeError("the health-issue registry is frozen")
        if not isinstance(source, IssueSource):
            raise TypeError("register an IssueSource")
        if any(existing.name == source.name for existing in self._sources):
            raise ValueError(f"issue source {source.name} is already registered")
        self._sources.append(source)

    def freeze(self) -> None:
        """Refuse any further registration."""
        self._frozen = True


# Last successful runs ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LastRun:
    """A job type and its last successful run, ``None`` when it never succeeded."""

    job_type: str
    succeeded_at: datetime | None


@dataclass(frozen=True, slots=True)
class GroupRuns:
    """One job group's types and their last successful runs."""

    group: JobGroup
    runs: tuple[LastRun, ...]


def job_group_runs(
    job_types: Iterable[tuple[str, JobGroup]], last: Mapping[str, datetime]
) -> tuple[GroupRuns, ...]:
    """Each job group that has a type, in ``JobGroup`` order, with its types' last successes
    in registration order; see the module docstring."""
    by_group: dict[JobGroup, list[LastRun]] = {}
    for name, group in job_types:
        by_group.setdefault(JobGroup(group), []).append(LastRun(name, last.get(name)))
    return tuple(
        GroupRuns(group, tuple(by_group[group])) for group in JobGroup if group in by_group
    )
