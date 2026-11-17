"""Public command API of the ``ops`` service.

Other services and the delivery layers change ``ops`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``DashboardEntry`` (AD-13, FR-56) issues and consumes the one-time code that opens the
dashboard. It keeps the code in the keyring entry ``OPEN_CODE_SECRET`` through ``SecretsPort``
and touches no database, so its methods are not ``@use_case``s and need no unit of work:

- ``issue_code()`` (``nextron open``) writes a fresh code with its issue time and returns it; an
  ``AdapterError`` propagates.
- ``withdraw_code()`` deletes the entry; an ``AdapterError`` propagates.
- ``consume_code(presented)`` (``GET /enter``) returns a ``CodeCheck``, one caller at a time:
  a failed read is ``unavailable``; ``accepted`` deletes the entry first and becomes
  ``unavailable`` when that delete fails, so a code is never accepted twice; ``expired`` and
  ``malformed`` delete it on a best-effort basis; ``mismatch`` and ``absent`` delete nothing, so
  a junk request from another website can't cancel a pending code.

``Heartbeat(clock, heartbeat_file)`` (AD-20, NFR-14) writes the engine's heartbeat. The
heartbeat writer (``nextron.jobs.heartbeat``) calls it every 30 s while the runner, its jobs and
the scheduler are healthy, and the supervisor (``scripts/run.ps1``) restarts the engine when
the file goes stale.

- ``@use_case beat()`` reads the clock once. In one transaction it saves the ``runner_heartbeat``
  row: ``beat_at`` = now, ``pid`` = ``os.getpid()`` and ``started_at`` = the time of this
  object's first ``beat()`` call, kept for its life (the engine's start, within a second). After
  the commit it touches ``heartbeat_file``, creating it when it is missing. The row comes
  first, so a database that keeps failing leaves the file stale too, and a failed row write
  touches no file. Any failure raises to the caller. It writes no ``events`` row, and neither
  the row nor the file holds personal data.

``DeadJobAlerts(notify)`` (AD-15, AD-20) raises the ``job_dead`` ops alert. jobqueue sits below
notify in the app DAG, so it can't raise the alert itself, and a job dies in four places (a
failed run, a reaped lease, an unrunnable claim and a release at shutdown); this sweep reads
only committed ``dead`` rows instead. The runner (``nextron.jobs.runner``) calls it after a run
whose job died and on every keeper tick.

- ``@use_case alert_dead_jobs() -> int``: in one transaction, for every ``dead`` job
  (``queries.jobqueue.dead_jobs()``) whose id isn't among ``queries.notify.subjects("job_dead")``,
  it calls ``notify.alert("job_dead", <job id>, job_dead_text(type, id, attempts), channels)``
  with ``{telegram, dashboard}``, or ``{dashboard}`` for a dead ``send_alert`` job, so a broken
  Telegram can't feed itself. Each job is alerted once, a crash between a job's death and the
  sweep included. It returns how many it alerted and logs ``dead_jobs_alerted`` (``count``)
  when that is above 0.

``Budget(clock, meter, notify)`` (AD-8, FR-57, NFR-11, NFR-12, UX-DR28) reads the month's usage
against the quotas and the AI budget (``nextron.domain.budget``) and raises the threshold
notices. ``nextron.main`` builds it and runs ``check`` right after each usage row is recorded
(the meter's ``after_record``); the Quotas and cost page reads ``status``.

- ``status() -> Assessment`` is a plain read, with no use case or transaction, so a dashboard
  GET can call it in its read-only unit of work: it reads the clock once, then returns
  ``assess(meter.snapshot(now), queries.setup.budget_limits())``.
- ``@use_case check() -> tuple[Threshold, ...]``: in one transaction, ``notify.alert(kind,
  subject, text, {dashboard})`` for each of ``thresholds(status())``. Each notice is unique per
  kind and subject, and its subject names the month, so it comes once a month (the AI warning,
  the AI budget reached, and each quota reached); raising a limit mid-month doesn't repeat it.
  It returns the thresholds that added a notification and, after the commit, logs
  ``usage_alert_raised`` (INFO, ``kind`` and ``subject``) for each. Dashboard only: no Telegram.
"""

import contextlib
import logging
import os
import secrets
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final

from nextron.app.notify.api import Notify
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.uow import current, use_case
from nextron.domain.alert import JOB_DEAD, SEND_ALERT_JOB, Channel, job_dead_text
from nextron.domain.budget import Assessment, Threshold, assess, thresholds
from nextron.domain.open_code import CodeCheck, check, encode
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort
from nextron.ports.usage_meter import UsageMeterPort

OPEN_CODE_SECRET: Final = "session/open_code"
ALL_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)
DASHBOARD_ONLY: Final = (Channel.DASHBOARD,)  # a dead send_alert job: never Telegram again

log = logging.getLogger(__name__)


def new_open_code() -> str:
    """A fresh 256-bit code: 43 base64url characters."""
    return secrets.token_urlsafe(32)


class DashboardEntry:
    """The one-time code that opens the dashboard."""

    def __init__(
        self,
        secrets: SecretsPort,
        clock: Clock,
        *,
        new_code: Callable[[], str] = new_open_code,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._new_code = new_code
        self._lock = threading.Lock()

    def issue_code(self) -> str:
        """Store a fresh code with its issue time, replacing any pending one, and return it."""
        with self._lock:
            code = self._new_code()
            self._secrets.write(OPEN_CODE_SECRET, encode(code, self._clock.now()))
            return code

    def withdraw_code(self) -> None:
        """Delete the pending code, if any."""
        with self._lock:
            self._secrets.delete(OPEN_CODE_SECRET)

    def consume_code(self, presented: str) -> CodeCheck:
        """Check ``presented`` against the pending code; delete the code when it is used up."""
        with self._lock:
            try:
                stored = self._secrets.read(OPEN_CODE_SECRET)
            except AdapterError:
                return CodeCheck.UNAVAILABLE
            result = check(stored, presented, self._clock.now())
            if result is CodeCheck.ACCEPTED:
                try:
                    self._secrets.delete(OPEN_CODE_SECRET)
                except AdapterError:
                    return CodeCheck.UNAVAILABLE
            elif result in (CodeCheck.EXPIRED, CodeCheck.MALFORMED):
                with contextlib.suppress(AdapterError):
                    self._secrets.delete(OPEN_CODE_SECRET)
            return result


class Heartbeat:
    """The engine's heartbeat: the ``runner_heartbeat`` row, then the heartbeat file."""

    def __init__(self, clock: Clock, heartbeat_file: Path) -> None:
        self._clock = clock
        self._file = heartbeat_file
        self._started_at: datetime | None = None

    @use_case
    def beat(self) -> None:
        """Save the heartbeat row in one transaction, then touch the heartbeat file."""
        now = self._clock.now()
        if self._started_at is None:
            self._started_at = now
        with current().transaction():
            heartbeat_repo.save(pid=os.getpid(), started_at=self._started_at, beat_at=now)
        self._file.touch()


class DeadJobAlerts:
    """The ``job_dead`` sweep: one ops alert per dead job; see the module docstring."""

    def __init__(self, notify: Notify) -> None:
        self._notify = notify

    @use_case
    def alert_dead_jobs(self) -> int:
        """Alert every dead job not yet alerted; how many were alerted."""
        count = 0
        with current().transaction():
            alerted = notify_queries.subjects(JOB_DEAD)
            for job in jobqueue_queries.dead_jobs():
                if job.id in alerted:
                    continue
                channels = DASHBOARD_ONLY if job.type == SEND_ALERT_JOB else ALL_CHANNELS
                text = job_dead_text(job.type, job.id, job.attempts)
                self._notify.alert(JOB_DEAD, job.id, text, channels)
                count += 1
        if count > 0:
            log.info("dead_jobs_alerted", extra={"count": count})
        return count


class Budget:
    """The month's usage against the quotas and the AI budget, and its threshold notices; see
    the module docstring."""

    def __init__(self, clock: Clock, meter: UsageMeterPort, notify: Notify) -> None:
        self._clock = clock
        self._meter = meter
        self._notify = notify

    def status(self) -> Assessment:
        """This month's assessment, read in the current unit of work (a read-only one
        included)."""
        now = self._clock.now()
        return assess(self._meter.snapshot(now), setup_queries.budget_limits())

    @use_case
    def check(self) -> tuple[Threshold, ...]:
        """Raise each threshold notice not yet raised this month; the ones it raised."""
        raised: list[Threshold] = []
        with current().transaction():
            for threshold in thresholds(self.status()):
                alerted = self._notify.alert(
                    threshold.kind, threshold.subject, threshold.text, DASHBOARD_ONLY
                )
                if alerted.created:
                    raised.append(threshold)
        for threshold in raised:
            log.info(
                "usage_alert_raised", extra={"kind": threshold.kind, "subject": threshold.subject}
            )
        return tuple(raised)
