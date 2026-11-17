"""The health panel (FR-58, AD-10, AD-20, OP-9, UX-DR5, UX-DR29): whether the engine is healthy,
on one page.

``create_app`` serves it from ``EngineHealth.report()`` (the ``ops`` service), through
``panel_context``:

- ``GET /health/panel`` (``health_page``, the navigation's Health section) renders
  ``health.html``: the layout around ``{% include "health_panel.html" %}``;
- ``GET /health/panel/live`` (``health_panel``) renders ``health_panel.html`` alone. The panel
  is ``<div id="health-panel" hx-get="/health/panel/live" hx-trigger="every 30s"
  hx-swap="outerHTML">``, so HTMX re-fetches it every 30 seconds while the page is open;
- ``POST /health/jobs/dismiss`` (``health_job_dismiss``) dismisses a dead job: apart from
  ``csrf_token`` its fields must be exactly one ``job`` holding a ULID (``submitted_job``;
  anything else is ``MalformedForm``, 400). It deletes the job through ``JobQueue.dismiss`` in
  its own unit of work, then renders ``health_panel.html`` in a read-only one;
- ``POST /health/backup`` (``health_backup_now``, Story 1.16) is "Back up now": its only field
  is ``csrf_token`` (``submitted_backup``; anything else is ``MalformedForm``, 400). It queues a
  manual backup through ``Backups.request_backup`` in its own unit of work, then renders
  ``health_panel.html`` in a read-only one with a notice: ``BACKUP_QUEUED`` when the job was
  created, ``BACKUP_ALREADY_QUEUED`` when one was already queued or running;
- ``POST /health/restore-test`` (``health_restore_test_now``, Story 1.18) is "Run restore test
  now": its only field is ``csrf_token`` (``submitted_restore_test``; anything else is
  ``MalformedForm``, 400). It queues a manual restore test through
  ``RestoreTests.request_restore_test`` in its own unit of work, then renders
  ``health_panel.html`` in a read-only one with a notice: ``RESTORE_TEST_QUEUED`` when the job
  was created, ``RESTORE_TEST_ALREADY_QUEUED`` when one was already queued or running.

All five are ``@personal_data(True)``: a dead job's ``last_error`` and a failed backup's error
are free text that may hold personal data (redacted when stored and again when read), so every
answer is ``no-store``.

In order, the panel shows:

1. "Checked at <YYYY-MM-DD HH:MM:SS> UTC; refreshes every 30 seconds.";
2. **Worker**: "Running (last beat <time> UTC)" while the heartbeat is under 2 minutes old,
   else "Not running: last beat <time> UTC", or "Not running: no heartbeat yet";
3. **Backups**: "No backup yet." or "Last backup: <YYYY-MM-DD HH:MM:SS UTC>, <status> (<kind
   label>)" from the newest backup's ``started_at``, followed by ", <off-PC label>" when its
   off-PC outcome is recorded (Story 1.17: "copied off the PC", "off-PC copy failed" or
   "off-PC not configured"). A failed backup, or a failed off-PC copy, gets the
   ``health-backup-failed`` class. Then the notice of a "Back up now" click
   (``<p class="health-backup-notice" role="status">``) when there is one, then the "Back up
   now" form (``id="backup-now"``);
4. **Restore test** (Story 1.18): "No restore test yet." or "Last restore test: <YYYY-MM-DD
   HH:MM:SS UTC>, <result> (<kind label>)" from the newest test's ``started_at``; a failed test
   adds ": <reason>; failing checks: <check labels, comma-separated>" and gets the
   ``health-restore-failed`` class. Then the notice of a "Run restore test now" click
   (``<p class="health-restore-notice" role="status">``) when there is one, then the "Run
   restore test now" form (``id="restore-test-now"``);
5. **Errors needing attention (<n>)**: each issue's text and its detail (escaped with
   ``|plain``); a ``job_dead`` issue has a Dismiss form. With none: "No errors need
   attention.";
6. **Last successful runs**: one table per job group (Job type, Last success), "never" for a
   type that has not succeeded;
7. **Readiness (nextron doctor)**: a table (Status, Check, Message) and "<n> ok, <n> warn, <n>
   fail".
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nextron.app.ops.api import HealthReport, Worker
from nextron.app.queries.ops import BackupRow, RestoreTestRow
from nextron.domain.alert import JOB_DEAD
from nextron.domain.backup import BackupKind, BackupStatus, OffpcStatus
from nextron.domain.ids import is_ulid
from nextron.domain.readiness import tally
from nextron.domain.restore_test import CheckOutcome, RestoreCheck, RestoreResult, RestoreTestKind
from nextron.web.csrf import CSRF_FIELD
from nextron.web.forms import REPEATED_FIELD, WRONG_FIELDS, FormFields, MalformedForm

PAGE_TEMPLATE: Final = "health.html"
PANEL_TEMPLATE: Final = "health_panel.html"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S UTC"
JOB_FIELD: Final = "job"
NEVER: Final = "never"
NO_ERRORS: Final = "No errors need attention."
NO_HEARTBEAT: Final = "Not running: no heartbeat yet"
NO_BACKUP: Final = "No backup yet."
BACKUP_QUEUED: Final = "Backup queued; its result shows here when it finishes."
BACKUP_ALREADY_QUEUED: Final = "A backup is already queued or running."
NO_RESTORE_TEST: Final = "No restore test yet."
RESTORE_TEST_QUEUED: Final = "Restore test queued; its result shows here when it finishes."
RESTORE_TEST_ALREADY_QUEUED: Final = "A restore test is already queued or running."


@dataclass(frozen=True, slots=True)
class IssueItem:
    """One error as the panel shows it; ``job`` is the dead job's ULID for its Dismiss form,
    ``None`` for any other issue."""

    text: str
    detail: str | None
    job: str | None


@dataclass(frozen=True, slots=True)
class RunRow:
    """One job type's row: its name and its last success, or "never"."""

    job_type: str
    last_success: str


@dataclass(frozen=True, slots=True)
class GroupTable:
    """One job group's heading and rows."""

    label: str
    rows: tuple[RunRow, ...]


@dataclass(frozen=True, slots=True)
class ReadinessRow:
    """One readiness check's row."""

    status: str
    check: str
    message: str


def shown_time(moment: datetime) -> str:
    """``YYYY-MM-DD HH:MM:SS UTC``."""
    return f"{moment.astimezone(UTC):{TIME_FORMAT}}"


def worker_text(worker: Worker) -> str:
    """The worker's line: running, not running since the last beat, or never beat."""
    if worker.beat_at is None:
        return NO_HEARTBEAT
    if worker.running:
        return f"Running (last beat {shown_time(worker.beat_at)})"
    return f"Not running: last beat {shown_time(worker.beat_at)}"


def backup_text(backup: BackupRow | None) -> str:
    """The Backups line: the newest backup's start, status and kind, and its off-PC outcome
    when there is one, or "No backup yet."."""
    if backup is None:
        return NO_BACKUP
    label = BackupKind(backup.kind).label
    text = f"Last backup: {shown_time(backup.started_at)}, {backup.status} ({label})"
    if backup.offpc_status is not None:
        text += f", {OffpcStatus(backup.offpc_status).label}"
    return text


def backup_failed(backup: BackupRow | None) -> bool:
    """Whether the Backups line shows a failure: the backup, or its off-PC copy, failed."""
    return backup is not None and (
        backup.status == BackupStatus.FAILED or backup.offpc_status == OffpcStatus.FAILED
    )


def restore_test_text(test: RestoreTestRow | None) -> str:
    """The Restore test line: the newest test's start, result and kind, and for a failed one
    its reason and failing checks, or "No restore test yet."."""
    if test is None:
        return NO_RESTORE_TEST
    label = RestoreTestKind(test.kind).label
    text = f"Last restore test: {shown_time(test.started_at)}, {test.result} ({label})"
    if test.result == RestoreResult.FAIL:
        failing = ", ".join(
            check.label
            for check, outcome in zip(RestoreCheck, test.checks, strict=True)
            if outcome == CheckOutcome.FAIL
        )
        text += f": {test.reason}; failing checks: {failing}"
    return text


def restore_test_failed(test: RestoreTestRow | None) -> bool:
    """Whether the Restore test line shows a failure."""
    return test is not None and test.result == RestoreResult.FAIL


def panel_context(
    report: HealthReport, *, notice: str | None = None, restore_notice: str | None = None
) -> dict[str, object]:
    """What ``health_panel.html`` renders for ``report``, with the "Back up now" ``notice``
    and the "Run restore test now" ``restore_notice`` when there is one."""
    issues = [
        IssueItem(issue.text, issue.detail, issue.subject if issue.kind == JOB_DEAD else None)
        for issue in report.issues
    ]
    groups = [
        GroupTable(
            group.group.label,
            tuple(
                RunRow(
                    run.job_type,
                    NEVER if run.succeeded_at is None else shown_time(run.succeeded_at),
                )
                for run in group.runs
            ),
        )
        for group in report.groups
    ]
    readiness = [
        ReadinessRow(result.status.value, result.id, result.message)
        for result in report.readiness
    ]
    ok, warn, fail = tally(report.readiness)
    return {
        "checked_at": shown_time(report.checked_at),
        "worker": worker_text(report.worker),
        "worker_running": report.worker.running,
        "backup": backup_text(report.backup),
        "backup_failed": backup_failed(report.backup),
        "backup_notice": notice,
        "restore_test": restore_test_text(report.restore_test),
        "restore_test_failed": restore_test_failed(report.restore_test),
        "restore_notice": restore_notice,
        "issues": issues,
        "no_errors": NO_ERRORS,
        "groups": groups,
        "readiness": readiness,
        "readiness_counts": f"{ok} ok, {warn} warn, {fail} fail",
    }


def submitted_job(fields: FormFields) -> str:
    """The one ``job`` ULID a Dismiss form posts; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD} != {JOB_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
    values = fields[JOB_FIELD]
    if len(values) != 1:
        raise MalformedForm(REPEATED_FIELD)
    if not is_ulid(values[0]):
        raise MalformedForm(WRONG_FIELDS)
    return values[0]


def submitted_backup(fields: FormFields) -> None:
    """A "Back up now" form posts only its ``csrf_token``; ``MalformedForm`` otherwise."""
    if set(fields) - {CSRF_FIELD}:
        raise MalformedForm(WRONG_FIELDS)


def submitted_restore_test(fields: FormFields) -> None:
    """A "Run restore test now" form posts only its ``csrf_token``; ``MalformedForm``
    otherwise."""
    if set(fields) - {CSRF_FIELD}:
        raise MalformedForm(WRONG_FIELDS)
