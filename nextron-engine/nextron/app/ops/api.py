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
- ``health_issues() -> tuple[HealthIssue, ...]`` is ``budget_issues(status())``, a plain read:
  the health panel's ``budget`` issue source (AI spend at the warning percentage or more, the
  AI budget reached, each quota reached; never a limit of 0). Usage is readable only through
  ``UsageMeterPort``, so this source is ``ops``'s, not a ``queries`` module's.

``EngineHealth(clock, job_types, *, issues, readiness)`` (AD-10, AD-20, FR-58, OP-9) assembles
the health panel. ``issues`` is the frozen health-issue registry
(``nextron.main.build_health_issues``) and ``readiness`` runs the ``nextron doctor`` checks in
the engine (no Google refresh, no port probe). Its methods are plain reads, with no use case or
transaction, for a dashboard GET's read-only unit of work:

- ``issues() -> tuple[HealthIssue, ...]`` runs each registered source in order. A source that
  raises, or yields anything but a ``HealthIssue``, gives instead the one issue
  ``HealthIssue("health_check_failed", <source name>, "The <name> health check could not be
  read (<ExceptionType>)")`` and logs ``health_check_failed`` (ERROR, ``source``, ``code`` and
  the traceback); the other sources still run.
- ``issue_count() -> int`` is ``len(issues())``: the notification area's error count.
- ``report() -> HealthReport(checked_at, worker, groups, issues, readiness, backup)`` reads
  the clock once, then runs ``readiness()`` before its own database reads. Readiness ends with the
  checks that read the database (``required_settings``, ``database_revision``), which begin the
  read transaction; the checks that reach Credential Manager or the registry are registered
  before them (``nextron.main.build_doctor_checks``), so they never hold its lock (AD-2's
  ``BEGIN IMMEDIATE`` covers reads). ``worker`` is ``Worker(running, beat_at)`` from
  ``queries.ops.heartbeat()`` and ``nextron.domain.health.worker_running`` (``beat_at`` is
  ``None`` before the first beat); ``groups`` is ``job_group_runs`` of the registered job types
  (by ``JobType.group``) and ``queries.jobqueue.last_successes()``; ``backup`` is
  ``queries.ops.last_backup()``, the newest backup of any kind and status, read after
  readiness (``None`` before the first one).

**The seed sync (FR-1, FR-2, FR-3, FR-4, FR-72, AD-3, AD-12; Stories 2.1, 3.1, 4.1, 4.2 and
4.3).** ``Seeds(clock)`` is what ``nextron seed sync`` calls. ``setup`` (the Segments, Markets,
Sequences, Offers and Campaigns) and ``playbook`` (the Approved Company Facts) sit in the same
app-DAG layer, so neither may call the other; this use case in a higher layer calls each
owner's command in one transaction (decision 1).

- ``@use_case sync_seed(segments, markets, sequences, company_facts, offers, campaigns) ->
  SeedSync(segments, markets, sequences, company_facts, offers, campaigns)`` (each story's
  arguments and results after the earlier ones): (1) reads the clock once; (2) runs
  ``setup.api.check_seed`` (the Segments, Markets, Sequences, Offers and Campaigns), then
  ``playbook.api.check_seed_facts`` against that sync's Segment keys and Market countries, with
  ``latest`` the UTC date of now plus one day; (3) in **one** transaction runs
  ``Setup(clock).seed_sync(...)``, then ``CompanyFacts(clock).seed_facts(...)``, so a failure
  anywhere changes nothing in any seeded table; (4) after the commit runs ``log_seed_sync``,
  then ``log_fact_sync``, and returns ``SeedSync``. A check that fails raises before any
  transaction (``TypeError``, ``ValueError``, ``InvalidSegments``, ``InvalidMarkets``,
  ``InvalidSequences``, ``InvalidOffers``, ``InvalidCampaigns`` or ``InvalidCompanyFacts``).

**Backups (Story 1.16; AD-2, AD-20, FR-58, OP-5, OP-6, NFR-16).** Every copy is made with
SQLite's backup API (``nextron.db.repos.ops.snapshot``), verified and recorded in ``backups``,
once, with no state machine and no ``events`` row (backups aren't one of AD-4's machines).

``TakenBackup(kind, path, started_at, finished_at, snapshot, error, code)`` is one copy as
taken: ``snapshot`` (a verified ``Snapshot``) or ``error`` and ``code`` (why it failed), never
both.

``LocalBackups(clock, *, env, local_dir, snapshot)`` takes and records local copies in
``local_dir`` (``<backup dir>\\local``); ``snapshot(path)`` copies and verifies
(``nextron.main`` binds ``snapshot.snapshot`` to ``<data dir>\\nextron.db``):

- ``take(kind) -> TakenBackup`` touches no database, so ``serve`` can call it inside the
  migration's write transaction. It reads the clock, names the copy ``local_dir /
  copy_name(env, now)`` and calls ``snapshot``. Any ``Exception`` becomes ``error``
  (``jobqueue.error_text``) and ``code`` (``jobqueue.error_code``): it never raises. It then
  reads the clock again for ``finished_at``.
- ``@command insert(taken) -> str`` writes the row in the caller's transaction (``verified``
  with the snapshot's size, SHA-256, revision and counts, or ``failed`` with the error) and
  returns its ULID.
- ``rotate() -> int`` runs inside a running use case, after a verified backup. In one
  transaction it reads ``backup_rotation_days`` (a value that doesn't parse skips rotation and
  logs ``backup_rotation_skipped``), the newest ``verified`` row (always kept) and the
  candidates (``verified``, copy not yet deleted, started more than that many days ago). Outside
  any transaction it deletes (``discard``) each candidate whose file is directly in
  ``local_dir`` (compared with ``normcase`` and ``abspath``) and whose name passes
  ``is_copy_name(env, ...)``; any other is skipped and logged (``backup_rotation_skipped``,
  WARNING, ``backup_id``, ``reason``). A file with no row (a crash mid-copy, or a
  ``pre_migrate`` copy whose migration failed) is never deleted. In a second transaction it
  marks each copy that is gone (``local_deleted_at``); a copy that couldn't be deleted (locked)
  is tried again next time. It logs ``backups_rotated`` (INFO, ``count``) when it deleted any,
  and returns the count. ``back_up`` and ``record_pre_migrate`` call it after their row has
  committed, guarded: a rotation that raises (a locked database, say) logs
  ``backup_rotation_failed`` (ERROR: ``backup_id``, ``code``, the traceback) and the backup
  still succeeds, so a recorded backup is never reported as failed; the next backup rotates
  again.
- ``@use_case record_pre_migrate(taken) -> str`` records a verified ``pre_migrate`` copy
  (``ValueError`` for anything else) after ``serve``'s upgrade succeeded: one transaction, then
  ``backup_completed``, then the guarded ``rotate()``; it returns the row's ULID.

``Backups(clock, local, *, notify, queue)`` is the ``backup_db`` job's service and the health
panel's "Back up now":

- ``@use_case back_up(kind) -> str`` takes a ``JOB_KINDS`` kind only (``ValueError``
  otherwise). Under one ``threading.Lock``, so two backups never copy at once, it calls
  ``take(kind)``, then in one transaction ``insert``s the row and, for a failed copy, raises the
  ``backup_failed`` ops alert (``notify.alert(BACKUP_FAILED, <id>, backup_failed_text(kind,
  id), (telegram, dashboard))``). If that transaction raises, a verified copy is deleted
  (never a copy without a row), ``backup_record_failed`` is logged (ERROR: ``kind``, ``code``,
  ``file``, the traceback) and it raises ``AdapterError(retryable=True,
  code="backup_not_recorded")`` from the error, so jobqueue retries the job (retry limit 2; a
  retry takes a new copy). After the commit it logs
  ``backup_completed`` (INFO: ``backup_id``, ``kind``, ``size_bytes``, ``file``) or
  ``backup_failed`` (ERROR: ``backup_id``, ``kind``, ``code``); a verified backup then rotates
  (guarded: a failed rotation is logged and the backup's id is still returned).
  A failed copy never raises, so the job never retries it: the ``failed`` row, the alert and
  the health panel report it, and the next daily time or "Back up now" tries again.
- ``@use_case request_backup() -> Enqueued`` enqueues the ``backup_db`` job (scope ``global``,
  discriminator the UTC date, payload ``{"v": 1, "kind": "manual"}``) in one transaction, so a
  click while a manual backup is queued or running is absorbed (``created=False``). After the
  commit it logs ``backup_requested`` (INFO, ``job_id``, ``job_created``).
- ``schedule() -> BackupSchedule`` is a plain read for a read-only unit of work: ``at`` is
  ``backup_time``, ``timezone`` is ``sender_timezone`` (``"UTC"`` while it is unset) and
  ``missed`` is ``missed_daily`` of the newest ``daily`` row's ``started_at``. ``nextron.main``
  reads it once, when the scheduler starts.

Logs carry IDs, kinds, codes, counts and file names only, never a full path or an error's text.
"""

import contextlib
import logging
import os
import secrets
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from nextron.app.jobqueue.api import Enqueued, JobQueue, error_code, error_text
from nextron.app.notify.api import Notify
from nextron.app.playbook.api import (
    CompanyFacts,
    CompanyFactSync,
    check_seed_facts,
    latest_allowed,
    log_fact_sync,
)
from nextron.app.queries import jobqueue as jobqueue_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import ops as ops_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import (
    CampaignSync,
    MarketSync,
    OfferSync,
    SegmentSync,
    SequenceSync,
    Setup,
    check_seed,
    log_seed_sync,
)
from nextron.db.repos.ops import backups as backups_repo
from nextron.db.repos.ops import heartbeat as heartbeat_repo
from nextron.db.repos.ops.backups import BackupRow
from nextron.db.repos.ops.snapshot import Snapshot, discard
from nextron.db.uow import command, current, use_case
from nextron.domain.alert import JOB_DEAD, SEND_ALERT_JOB, Channel, job_dead_text
from nextron.domain.backup import (
    BACKUP_DB_JOB,
    BACKUP_FAILED,
    JOB_KINDS,
    KIND_FIELD,
    BackupKind,
    BackupSchedule,
    BackupStatus,
    backup_failed_text,
    copy_name,
    is_copy_name,
    is_env_name,
    missed_daily,
)
from nextron.domain.budget import Assessment, Threshold, assess, budget_issues, thresholds
from nextron.domain.campaign import CampaignDef
from nextron.domain.company_facts import SeedFact
from nextron.domain.health import (
    GroupRuns,
    HealthIssue,
    HealthIssues,
    IssueSource,
    job_group_runs,
    worker_running,
)
from nextron.domain.job import GLOBAL_SCOPE, JobTypes
from nextron.domain.markets import MarketDef
from nextron.domain.offers import OfferDef
from nextron.domain.open_code import CodeCheck, check, encode
from nextron.domain.readiness import CheckResult
from nextron.domain.segments import SegmentDef
from nextron.domain.sequences import SequenceDef
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.secrets import SecretsPort
from nextron.ports.usage_meter import UsageMeterPort

OPEN_CODE_SECRET: Final = "session/open_code"
HEALTH_CHECK_FAILED: Final = "health_check_failed"
ALL_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)
DASHBOARD_ONLY: Final = (Channel.DASHBOARD,)  # a dead send_alert job: never Telegram again
UTC_TIMEZONE: Final = "UTC"  # the daily backup's zone while sender_timezone is unset
PAYLOAD_VERSION: Final = 1
BACKUP_COMPLETED: Final = "backup_completed"
BACKUP_ROTATION_SKIPPED: Final = "backup_rotation_skipped"
BACKUP_RECORD_FAILED: Final = "backup_record_failed"
BACKUP_ROTATION_FAILED: Final = "backup_rotation_failed"
BACKUP_NOT_RECORDED: Final = "backup_not_recorded"  # the retryable AdapterError's code

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


@dataclass(frozen=True, slots=True)
class SeedSync:
    """What ``Seeds.sync_seed`` did: the Segments' sync, the Markets', the Sequences', the
    Approved Company Facts', the Offers' and the Campaigns'."""

    segments: SegmentSync
    markets: MarketSync
    sequences: SequenceSync
    company_facts: CompanyFactSync
    offers: OfferSync
    campaigns: CampaignSync


class Seeds:
    """``nextron seed sync``'s use case: the six seed files in one transaction; see the module
    docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def sync_seed(
        self,
        segments: Sequence[SegmentDef],
        markets: Sequence[MarketDef],
        sequences: Sequence[SequenceDef],
        company_facts: Sequence[SeedFact],
        offers: Sequence[OfferDef],
        campaigns: Sequence[CampaignDef],
    ) -> SeedSync:
        """Load the Segments, Markets, Sequences, Approved Company Facts, Offers and Campaigns
        in one transaction."""
        now = self._clock.now()
        checked = check_seed(segments, markets, sequences, offers, campaigns)
        checked_facts = check_seed_facts(
            company_facts, checked.segment_keys, checked.market_countries, latest_allowed(now)
        )
        with current().transaction():
            setup_result = Setup(self._clock).seed_sync(checked, now)
            fact_result = CompanyFacts(self._clock).seed_facts(checked_facts, now)
        log_seed_sync(setup_result)
        log_fact_sync(fact_result)
        return SeedSync(
            setup_result.segments,
            setup_result.markets,
            setup_result.sequences,
            fact_result,
            setup_result.offers,
            setup_result.campaigns,
        )


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

    def health_issues(self) -> tuple[HealthIssue, ...]:
        """This month's budget issues for the health panel, read in the current unit of work
        (a read-only one included)."""
        return budget_issues(self.status())


@dataclass(frozen=True, slots=True)
class Worker:
    """The job runner as the heartbeat shows it: running (a beat under 2 minutes old), and the
    last beat, ``None`` before the first one."""

    running: bool
    beat_at: datetime | None


@dataclass(frozen=True, slots=True)
class HealthReport:
    """What the health panel shows; see the module docstring."""

    checked_at: datetime
    worker: Worker
    groups: tuple[GroupRuns, ...]
    issues: tuple[HealthIssue, ...]
    readiness: tuple[CheckResult, ...]
    backup: BackupRow | None


def _source_failed(source: IssueSource, error: Exception) -> HealthIssue:
    log.error(
        HEALTH_CHECK_FAILED,
        exc_info=error,
        extra={"source": source.name, "code": error_code(error)},
    )
    return HealthIssue(
        HEALTH_CHECK_FAILED,
        source.name,
        f"The {source.name} health check could not be read ({type(error).__name__})",
    )


def _read_source(source: IssueSource) -> tuple[HealthIssue, ...]:
    """The source's issues, or the one ``health_check_failed`` issue when it can't be read."""
    try:
        found = tuple(source.read())
        for issue in found:
            if not isinstance(issue, HealthIssue):
                raise TypeError(f"issue source {source.name} yielded something else")
    except Exception as error:  # noqa: BLE001 - one broken source never hides the others
        return (_source_failed(source, error),)
    return found


class EngineHealth:
    """The health panel's report: the worker, each job type's last success, the errors needing
    attention and the readiness list; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        job_types: JobTypes,
        *,
        issues: HealthIssues,
        readiness: Callable[[], Sequence[CheckResult]],
    ) -> None:
        if not isinstance(job_types, JobTypes):
            raise TypeError("job_types must be a JobTypes registry")
        if not isinstance(issues, HealthIssues):
            raise TypeError("issues must be a HealthIssues registry")
        if not issues.frozen:
            raise RuntimeError("freeze the health-issue registry before reading it")
        self._clock = clock
        self._job_types = job_types
        self._issues = issues
        self._readiness = readiness

    def issues(self) -> tuple[HealthIssue, ...]:
        """Every source's issues, in registration order."""
        found: list[HealthIssue] = []
        for source in self._issues.sources:
            found.extend(_read_source(source))
        return tuple(found)

    def issue_count(self) -> int:
        """How many errors need attention."""
        return len(self.issues())

    def report(self) -> HealthReport:
        """The whole panel, read in the current unit of work: the readiness checks first (their
        Credential Manager and registry reads before their database ones), then the rest."""
        now = self._clock.now()
        readiness = tuple(self._readiness())  # its last checks begin the read transaction
        backup = ops_queries.last_backup()
        beat = ops_queries.heartbeat()
        worker = Worker(
            beat is not None and worker_running(beat.beat_at, now),
            None if beat is None else beat.beat_at,
        )
        pairs = [(name, self._job_types.get(name).group) for name in self._job_types.names()]
        groups = job_group_runs(pairs, jobqueue_queries.last_successes())
        return HealthReport(now, worker, groups, self.issues(), readiness, backup)


# Backups ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TakenBackup:
    """One copy as taken: verified (``snapshot``) or failed (``error`` and ``code``)."""

    kind: BackupKind
    path: Path
    started_at: datetime
    finished_at: datetime
    snapshot: Snapshot | None
    error: str | None
    code: str | None

    def __post_init__(self) -> None:
        if (self.snapshot is None) == (self.error is None):
            raise ValueError("a taken backup has either a snapshot or an error")

    @property
    def verified(self) -> bool:
        """Whether the copy was made and verified."""
        return self.snapshot is not None


def _backup_completed(backup_id: str, taken: TakenBackup) -> None:
    size = None if taken.snapshot is None else taken.snapshot.size_bytes
    log.info(
        BACKUP_COMPLETED,
        extra={
            "backup_id": backup_id,
            "kind": taken.kind,
            "size_bytes": size,
            "file": taken.path.name,
        },
    )


def _rotate_after(local: "LocalBackups", backup_id: str) -> None:
    """Rotate after the recorded backup ``backup_id``; a failed rotation is logged, never
    raised, because the backup itself succeeded."""
    try:
        local.rotate()
    except Exception as exc:  # noqa: BLE001 - the backup is recorded; the next one rotates
        log.exception(
            BACKUP_ROTATION_FAILED, extra={"backup_id": backup_id, "code": error_code(exc)}
        )


def _same_folder(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


class LocalBackups:
    """Local copies in ``local_dir``: take, record and rotate them; see the module
    docstring."""

    def __init__(
        self,
        clock: Clock,
        *,
        env: str,
        local_dir: Path,
        snapshot: Callable[[Path], Snapshot],
    ) -> None:
        if not is_env_name(env):
            raise ValueError("env must be an environment name such as dev, test or prod")
        if not isinstance(local_dir, Path):
            raise TypeError("local_dir must be a pathlib.Path")
        if not callable(snapshot):
            raise TypeError("snapshot must be callable")
        self._clock = clock
        self._env = env
        self._local_dir = local_dir
        self._snapshot = snapshot

    @property
    def local_dir(self) -> Path:
        return self._local_dir

    def take(self, kind: BackupKind) -> TakenBackup:
        """Copy and verify the database now; never raises for a failed copy, and touches no
        database table."""
        kind = BackupKind(kind)
        started_at = self._clock.now()
        path = self._local_dir / copy_name(self._env, started_at)
        made: Snapshot | None = None
        error: str | None = None
        code: str | None = None
        try:
            made = self._snapshot(path)
        except Exception as exc:  # noqa: BLE001 - a failed copy is recorded, never raised
            error, code = error_text(exc), error_code(exc)
        return TakenBackup(kind, path, started_at, self._clock.now(), made, error, code)

    @command
    def insert(self, taken: TakenBackup) -> str:
        """Record ``taken`` in the caller's transaction; return the new row's ULID."""
        made = taken.snapshot
        return backups_repo.insert(
            kind=str(taken.kind),
            status=str(BackupStatus.VERIFIED if made is not None else BackupStatus.FAILED),
            started_at=taken.started_at,
            finished_at=taken.finished_at,
            path=str(taken.path),
            size_bytes=None if made is None else made.size_bytes,
            sha256=None if made is None else made.sha256,
            db_revision=None if made is None else made.db_revision,
            suppression_count=None if made is None else made.suppression_count,
            provenance_count=None if made is None else made.provenance_count,
            error=None if made is not None else taken.error,
        )

    def _deletable(self, row: BackupRow) -> str | None:
        """Why ``row``'s copy must not be deleted, or ``None`` when it may."""
        path = Path(row.path)
        if not _same_folder(path.parent, self._local_dir):
            return "outside_local_dir"
        if not is_copy_name(self._env, path.name):
            return "not_a_copy_name"
        return None

    def rotate(self) -> int:
        """Delete the verified local copies older than ``backup_rotation_days``, keeping the
        newest verified one; how many were deleted. Called from a running use case."""
        uow = current()
        now = self._clock.now()
        candidates: Iterable[BackupRow] = ()
        unreadable: str | None = None
        with uow.transaction():
            try:
                days = int(setup_queries.get(Key.BACKUP_ROTATION_DAYS))
            except (InvalidSettingValue, SettingNotSet) as exc:
                unreadable = error_code(exc)
            else:
                kept = backups_repo.newest(status=str(BackupStatus.VERIFIED))
                candidates = backups_repo.rotation_candidates(
                    now - timedelta(days=days), None if kept is None else kept.id
                )
        if unreadable is not None:
            log.warning(
                BACKUP_ROTATION_SKIPPED, extra={"reason": "setting_unreadable", "code": unreadable}
            )
            return 0
        gone: list[str] = []
        for row in candidates:
            reason = self._deletable(row)
            if reason is not None:
                log.warning(BACKUP_ROTATION_SKIPPED, extra={"backup_id": row.id, "reason": reason})
                continue
            if discard(Path(row.path)):
                gone.append(row.id)
        if not gone:
            return 0
        with uow.transaction():
            backups_repo.mark_deleted(gone, now)
        log.info("backups_rotated", extra={"count": len(gone)})
        return len(gone)

    @use_case
    def record_pre_migrate(self, taken: TakenBackup) -> str:
        """Record the verified ``pre_migrate`` copy ``serve`` took before a successful upgrade,
        then rotate; return its ULID."""
        if taken.kind is not BackupKind.PRE_MIGRATE or taken.snapshot is None:
            raise ValueError("record_pre_migrate records a verified pre_migrate backup only")
        with current().transaction():
            backup_id = self.insert(taken)
        _backup_completed(backup_id, taken)
        _rotate_after(self, backup_id)
        return backup_id


class Backups:
    """Daily and manual backups and the daily schedule; see the module docstring."""

    def __init__(
        self, clock: Clock, local: LocalBackups, *, notify: Notify, queue: JobQueue
    ) -> None:
        if not isinstance(local, LocalBackups):
            raise TypeError("local must be a LocalBackups")
        self._clock = clock
        self._local = local
        self._notify = notify
        self._queue = queue
        self._lock = threading.Lock()  # one backup at a time

    @use_case
    def back_up(self, kind: BackupKind) -> str:
        """Take, verify and record a daily or manual backup; return its row's ULID."""
        if kind not in JOB_KINDS:
            raise ValueError("back_up takes a daily or manual backup only")
        kind = BackupKind(kind)
        with self._lock:
            taken = self._local.take(kind)
            try:
                with current().transaction():
                    backup_id = self._local.insert(taken)
                    if not taken.verified:
                        self._notify.alert(
                            BACKUP_FAILED,
                            backup_id,
                            backup_failed_text(kind, backup_id),
                            ALL_CHANNELS,
                        )
            except BaseException as exc:
                if taken.verified:
                    discard(taken.path)  # never a copy without a row
                if not isinstance(exc, Exception):
                    raise
                log.exception(
                    BACKUP_RECORD_FAILED,
                    extra={"kind": kind, "code": error_code(exc), "file": taken.path.name},
                )
                raise AdapterError(  # retryable, so jobqueue retries the job
                    True,
                    BACKUP_NOT_RECORDED,
                    f"the backup's row could not be recorded ({error_code(exc)})",
                ) from exc
            if not taken.verified:
                log.error(
                    BACKUP_FAILED,
                    extra={"backup_id": backup_id, "kind": kind, "code": taken.code},
                )
                return backup_id
            _backup_completed(backup_id, taken)
            _rotate_after(self._local, backup_id)
            return backup_id

    @use_case
    def request_backup(self) -> Enqueued:
        """Queue a manual backup, or find the one already queued or running today."""
        now = self._clock.now()
        with current().transaction():
            enqueued = self._queue.enqueue(
                BACKUP_DB_JOB,
                scope=GLOBAL_SCOPE,
                discriminator=now.astimezone(UTC).date().isoformat(),
                payload={"v": PAYLOAD_VERSION, KIND_FIELD: str(BackupKind.MANUAL)},
            )
        log.info(
            "backup_requested",
            extra={"job_id": enqueued.job_id, "job_created": enqueued.created},
        )
        return enqueued

    def schedule(self) -> BackupSchedule:
        """When the daily backup runs and whether the last one was missed, read in the
        current unit of work (a read-only one included)."""
        now = self._clock.now()
        at = setup_queries.get(Key.BACKUP_TIME)
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except SettingNotSet:
            timezone = UTC_TIMEZONE
        newest = ops_queries.newest_backup(BackupKind.DAILY)
        started = None if newest is None else newest.started_at
        return BackupSchedule(at, timezone, missed_daily(started, now, at, timezone))
