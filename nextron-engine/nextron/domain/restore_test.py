"""The restore test of the off-PC backups (Story 1.18; FR-58, AD-20, OP-7, NFR-16): the names,
kinds, checks, reasons, the verdict, the monthly schedule and the ops alert text. Standard
library only; a function that needs the time takes ``now``.

Once a month, and on demand from the health panel, ``ops`` fetches and decrypts the newest
off-PC copy into a work folder under the backup dir, opens it read-only and checks it; this
module decides what the checks found. Nothing here touches a file or the database.

**Names.** ``RESTORE_TEST_JOB`` (``restore_test``) is the job type and the AD-10 trigger of the
same name. ``RESTORE_TEST_FAILED`` (``restore_test_failed``) is both the ops alert kind of a
failed test and its health-issue kind. ``RESTORE_DIR_NAME`` (``restore_test``) is the folder
under the backup dir that holds each test's work folder. ``RESTORE_TEST_DELAY`` (1 hour) is how
long after the daily backup time the daily check runs, so it usually tests that morning's copy;
``DEFAULT_RESTORE_TEST_TIME`` (11:00) is the trigger's fallback, an hour after
``DEFAULT_BACKUP_TIME``. ``INTEGRITY_OK`` (``ok``) is the one ``integrity_check`` answer that
passes. ``MAX_DETAIL_LENGTH`` (500) is the longest detail kept.

**Kinds, results and checks.**

- ``RestoreTestKind`` is ``monthly`` (the daily trigger, once per calendar month) or ``manual``
  ("Run restore test now"); ``.label`` is "monthly" or "manual".
- ``RestoreResult`` is ``pass`` or ``fail``; ``CheckOutcome`` is ``pass``, ``fail`` or
  ``skipped``.
- ``RestoreCheck``, in the order they run: ``fetch``, ``integrity``, ``revision``,
  ``suppression`` and ``provenance``; ``.label`` is "fetch and decrypt", "integrity check",
  "Alembic revision", "Suppression List count" or "Provenance Records count".
- ``RestoreFailure`` is why a test failed. ``FETCH_FAILURES`` are the reasons of the ``fetch``
  check: ``no_offpc_copy``, ``offpc_not_configured``, ``copy_missing``, ``target_unavailable``,
  ``passphrase_not_set``, ``wrong_passphrase``, ``tampered``, ``truncated``, ``bad_format``,
  ``fetch_failed`` and ``checksum_mismatch``. The others are ``integrity_failed``,
  ``revision_behind``, ``revision_unknown``, ``suppression_fewer`` and ``provenance_fewer``.

**The verdict.** ``RestoreOutcome(result, checks, reason, detail, copy_revision,
suppression_count, provenance_count)`` is frozen: one ``CheckOutcome`` per ``RestoreCheck``, in
order; ``reason`` is the first failing check's ``RestoreFailure`` (``None`` on a pass);
``detail`` is at most 500 characters (``None`` on a pass). It refuses an inconsistent outcome
(``ValueError``): a ``pass`` with a failing check, a reason or a detail, or a ``fail`` without
both. ``failing`` is the checks that failed, in order, and ``check(name)`` one check's outcome.

- ``fetch_failure(reason, detail=None)``: ``fetch`` fails with a ``FETCH_FAILURES`` reason
  (``ValueError`` for another) and the other four checks are ``skipped``.
- ``judge(*, integrity, revision, head, revision_known, counts, recorded)``: ``fetch`` passed.
  ``integrity`` passes only for exactly ``"ok"``; otherwise it fails (``integrity_failed``,
  detail ``integrity_check: <text>``) and the other three are ``skipped``, with no revision or
  counts kept. ``revision`` passes when it equals ``head``; a known revision that isn't head
  (``revision_known``) is ``revision_behind``, anything else (``None`` included)
  ``revision_unknown``, with detail ``copy revision <rev or none>; code head <head>``. ``counts``
  (the copy's) and ``recorded`` (the backup row's) are ``(suppression, provenance)`` pairs, in
  ``COUNTED_TABLES`` order: a recorded ``None`` (the table was absent at backup time) is
  ``skipped``; a copy count that is ``None`` or below the recorded one fails
  (``suppression_fewer`` or ``provenance_fewer``, detail ``<table>: copy <n or absent>, backup
  <m>``); otherwise it passes. The result is ``pass`` when no check failed; the detail joins the
  failing details with ``"; "``, cut to 500 characters.

**The schedule.** "Monthly" means once per calendar month in ``timezone`` (the sender's
timezone, ``UTC`` while it is unset).

- ``restore_test_time(backup_time)`` is ``backup_time + RESTORE_TEST_DELAY``, wrapping past
  midnight (23:30 gives 00:30).
- ``restore_test_due(newest_monthly_started_at, now, timezone)``: ``None`` (no monthly test yet),
  or the newest monthly test started in an earlier calendar month than ``now``, both read in
  ``timezone``.
- ``missed_restore_test(newest, now, at, timezone)``: ``newest`` isn't ``None``, the test is due,
  and the latest slot (``backup.latest_slot(now, at, timezone)``) falls in ``now``'s calendar
  month in ``timezone``: the engine was off when this month's slot passed, so the test runs once
  at the next start. A brand-new database (no monthly test yet) is never missed: it waits for
  its first slot.
- ``RestoreTestSchedule(at, timezone, missed)`` is what ``ops`` reads for the trigger.

**Text.** ``restore_test_failed_text(kind, test_id, reason)`` is exactly ``"Restore test failed:
<kind> <ULID>, <reason>"``: ops alert text carries IDs and codes only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from zoneinfo import ZoneInfo

from nextron.domain.backup import COUNTED_TABLES, latest_slot
from nextron.domain.ids import is_ulid

RESTORE_TEST_JOB: Final = "restore_test"
RESTORE_TEST_FAILED: Final = "restore_test_failed"  # the alert kind and the health-issue kind
RESTORE_DIR_NAME: Final = "restore_test"
RESTORE_TEST_DELAY: Final = timedelta(hours=1)
DEFAULT_RESTORE_TEST_TIME: Final = time(11, 0)
INTEGRITY_OK: Final = "ok"
MAX_DETAIL_LENGTH: Final = 500
DETAIL_SEPARATOR: Final = "; "
_ANY_DAY: Final = date(2000, 1, 1)  # a day to add the delay to a time of day


class RestoreTestKind(StrEnum):
    """What started a restore test; ``label`` is how the health panel names it."""

    MONTHLY = "monthly"
    MANUAL = "manual"

    @property
    def label(self) -> str:
        return _KIND_LABELS[self]


_KIND_LABELS: Final = MappingProxyType(
    {RestoreTestKind.MONTHLY: "monthly", RestoreTestKind.MANUAL: "manual"}
)


class RestoreResult(StrEnum):
    """A restore test's overall result, written once."""

    PASS = "pass"
    FAIL = "fail"


class CheckOutcome(StrEnum):
    """One check's outcome."""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class RestoreCheck(StrEnum):
    """The checks, in the order they run; ``label`` is how the health panel names one."""

    FETCH = "fetch"
    INTEGRITY = "integrity"
    REVISION = "revision"
    SUPPRESSION = "suppression"
    PROVENANCE = "provenance"

    @property
    def label(self) -> str:
        return _CHECK_LABELS[self]


_CHECK_LABELS: Final = MappingProxyType(
    {
        RestoreCheck.FETCH: "fetch and decrypt",
        RestoreCheck.INTEGRITY: "integrity check",
        RestoreCheck.REVISION: "Alembic revision",
        RestoreCheck.SUPPRESSION: "Suppression List count",
        RestoreCheck.PROVENANCE: "Provenance Records count",
    }
)


class RestoreFailure(StrEnum):
    """Why a restore test failed: the first failing check's reason."""

    # the fetch check
    NO_OFFPC_COPY = "no_offpc_copy"
    OFFPC_NOT_CONFIGURED = "offpc_not_configured"
    COPY_MISSING = "copy_missing"
    TARGET_UNAVAILABLE = "target_unavailable"
    PASSPHRASE_NOT_SET = "passphrase_not_set"
    WRONG_PASSPHRASE = "wrong_passphrase"
    TAMPERED = "tampered"
    TRUNCATED = "truncated"
    BAD_FORMAT = "bad_format"
    FETCH_FAILED = "fetch_failed"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    # the other checks
    INTEGRITY_FAILED = "integrity_failed"
    REVISION_BEHIND = "revision_behind"
    REVISION_UNKNOWN = "revision_unknown"
    SUPPRESSION_FEWER = "suppression_fewer"
    PROVENANCE_FEWER = "provenance_fewer"


FETCH_FAILURES: Final = frozenset(
    {
        RestoreFailure.NO_OFFPC_COPY,
        RestoreFailure.OFFPC_NOT_CONFIGURED,
        RestoreFailure.COPY_MISSING,
        RestoreFailure.TARGET_UNAVAILABLE,
        RestoreFailure.PASSPHRASE_NOT_SET,
        RestoreFailure.WRONG_PASSPHRASE,
        RestoreFailure.TAMPERED,
        RestoreFailure.TRUNCATED,
        RestoreFailure.BAD_FORMAT,
        RestoreFailure.FETCH_FAILED,
        RestoreFailure.CHECKSUM_MISMATCH,
    }
)
_COUNT_FAILURES: Final = (RestoreFailure.SUPPRESSION_FEWER, RestoreFailure.PROVENANCE_FEWER)
_COUNT_CHECKS: Final = (RestoreCheck.SUPPRESSION, RestoreCheck.PROVENANCE)


def _cut(detail: str | None) -> str | None:
    return None if detail is None else detail[:MAX_DETAIL_LENGTH]


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    """What a restore test found; see the module docstring."""

    result: RestoreResult
    checks: tuple[CheckOutcome, ...]
    reason: RestoreFailure | None
    detail: str | None
    copy_revision: str | None = None
    suppression_count: int | None = None
    provenance_count: int | None = None

    def __post_init__(self) -> None:
        if len(self.checks) != len(RestoreCheck) or not all(
            isinstance(check, CheckOutcome) for check in self.checks
        ):
            raise ValueError("a restore outcome has one CheckOutcome per RestoreCheck")
        failed = CheckOutcome.FAIL in self.checks
        if self.result is RestoreResult.PASS:
            if failed or self.reason is not None or self.detail is not None:
                raise ValueError("a passed restore test has no failing check, reason or detail")
        elif self.result is RestoreResult.FAIL:
            if not failed or self.reason is None:
                raise ValueError("a failed restore test has a failing check and a reason")
        else:
            raise ValueError("a restore outcome's result must be a RestoreResult")
        if self.detail is not None and len(self.detail) > MAX_DETAIL_LENGTH:
            raise ValueError(f"a restore outcome's detail is at most {MAX_DETAIL_LENGTH} long")

    def check(self, name: RestoreCheck) -> CheckOutcome:
        """The outcome of the check ``name``."""
        return self.checks[list(RestoreCheck).index(RestoreCheck(name))]

    @property
    def failing(self) -> tuple[RestoreCheck, ...]:
        """The checks that failed, in order."""
        return tuple(
            check
            for check, outcome in zip(RestoreCheck, self.checks, strict=True)
            if outcome is CheckOutcome.FAIL
        )


def fetch_failure(reason: RestoreFailure, detail: str | None = None) -> RestoreOutcome:
    """The ``fetch`` check failed for ``reason``; the other checks are skipped."""
    reason = RestoreFailure(reason)
    if reason not in FETCH_FAILURES:
        raise ValueError(f"{reason} is not a reason of the fetch check")
    skipped = (CheckOutcome.SKIPPED,) * (len(RestoreCheck) - 1)
    return RestoreOutcome(
        RestoreResult.FAIL, (CheckOutcome.FAIL, *skipped), reason, _cut(detail)
    )


def _pair(name: str, values: Sequence[int | None]) -> tuple[int | None, ...]:
    found = tuple(values)
    if len(found) != len(COUNTED_TABLES):
        raise ValueError(f"{name} must hold one count per counted table ({len(COUNTED_TABLES)})")
    return found


def judge(
    *,
    integrity: str,
    revision: str | None,
    head: str,
    revision_known: bool,
    counts: Sequence[int | None],
    recorded: Sequence[int | None],
) -> RestoreOutcome:
    """The verdict on a fetched copy; see the module docstring."""
    copy_counts = _pair("counts", counts)
    backup_counts = _pair("recorded", recorded)
    if integrity != INTEGRITY_OK:
        skipped = (CheckOutcome.SKIPPED,) * (len(RestoreCheck) - 2)
        return RestoreOutcome(
            RestoreResult.FAIL,
            (CheckOutcome.PASS, CheckOutcome.FAIL, *skipped),
            RestoreFailure.INTEGRITY_FAILED,
            _cut(f"integrity_check: {integrity}"),
        )
    checks = [CheckOutcome.PASS, CheckOutcome.PASS]
    failures: list[tuple[RestoreFailure, str]] = []
    if revision is not None and revision == head:
        checks.append(CheckOutcome.PASS)
    else:
        checks.append(CheckOutcome.FAIL)
        known = revision is not None and revision_known
        reason = RestoreFailure.REVISION_BEHIND if known else RestoreFailure.REVISION_UNKNOWN
        shown = "none" if revision is None else revision
        failures.append((reason, f"copy revision {shown}; code head {head}"))
    for table, copied, kept, reason in zip(
        COUNTED_TABLES, copy_counts, backup_counts, _COUNT_FAILURES, strict=True
    ):
        if kept is None:
            checks.append(CheckOutcome.SKIPPED)
        elif copied is None or copied < kept:
            checks.append(CheckOutcome.FAIL)
            shown = "absent" if copied is None else str(copied)
            failures.append((reason, f"{table}: copy {shown}, backup {kept}"))
        else:
            checks.append(CheckOutcome.PASS)
    suppression, provenance = copy_counts
    if not failures:
        return RestoreOutcome(
            RestoreResult.PASS, tuple(checks), None, None, revision, suppression, provenance
        )
    return RestoreOutcome(
        RestoreResult.FAIL,
        tuple(checks),
        failures[0][0],
        _cut(DETAIL_SEPARATOR.join(detail for _, detail in failures)),
        revision,
        suppression,
        provenance,
    )


# The schedule -----------------------------------------------------------------------------------


def _check_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be an aware datetime")
    return value


def restore_test_time(backup_time: time) -> time:
    """The daily restore-test time: ``backup_time`` plus ``RESTORE_TEST_DELAY``, wrapping past
    midnight."""
    if not isinstance(backup_time, time):
        raise TypeError("backup_time must be a datetime.time")
    if backup_time.tzinfo is not None:
        raise ValueError("backup_time must be a naive time of day")
    return (datetime.combine(_ANY_DAY, backup_time) + RESTORE_TEST_DELAY).time()


def _month(moment: datetime, zone: ZoneInfo) -> tuple[int, int]:
    local = moment.astimezone(zone)
    return local.year, local.month


def restore_test_due(
    newest_monthly_started_at: datetime | None, now: datetime, timezone: str
) -> bool:
    """Whether a monthly test is due: none yet, or the newest started in an earlier calendar
    month than ``now``, both read in ``timezone``."""
    _check_aware("now", now)
    zone = ZoneInfo(timezone)
    if newest_monthly_started_at is None:
        return True
    newest = _check_aware("newest_monthly_started_at", newest_monthly_started_at)
    return _month(newest, zone) < _month(now, zone)


def missed_restore_test(newest: datetime | None, now: datetime, at: time, timezone: str) -> bool:
    """Whether this month's slot passed while the engine was off: a monthly test exists, one is
    due, and the latest slot at or before ``now`` is in ``now``'s calendar month."""
    if newest is None:
        return False
    if not restore_test_due(newest, now, timezone):
        return False
    zone = ZoneInfo(timezone)
    return _month(latest_slot(now, at, timezone), zone) == _month(now, zone)


@dataclass(frozen=True, slots=True)
class RestoreTestSchedule:
    """When the daily restore-test check runs (``at`` in ``timezone``), and whether this
    month's slot was missed, so the engine runs it once now."""

    at: time
    timezone: str
    missed: bool


# Text -------------------------------------------------------------------------------------------


def restore_test_failed_text(kind: str, test_id: str, reason: str) -> str:
    """The ops alert text of a failed restore test: its kind, its ULID and the reason code."""
    if not is_ulid(test_id):
        raise ValueError("test_id must be a ULID")
    return (
        f"Restore test failed: {RestoreTestKind(kind).value} {test_id}, "
        f"{RestoreFailure(reason).value}"
    )
