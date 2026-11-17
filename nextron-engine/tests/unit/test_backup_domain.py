"""``nextron.domain.backup`` (Story 1.16): copy names, the daily slot across DST and in UTC,
``missed_daily``, the kind labels and the alert text."""

from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nextron.domain.backup import (
    BACKUP_DB_JOB,
    BACKUP_FAILED,
    COUNTED_TABLES,
    DEFAULT_BACKUP_TIME,
    DEFAULT_ROTATION_DAYS,
    JOB_KINDS,
    KIND_FIELD,
    LOCAL_DIR_NAME,
    MAX_ROTATION_DAYS,
    MIN_ROTATION_DAYS,
    BackupKind,
    BackupNotVerified,
    BackupSchedule,
    BackupStatus,
    backup_failed_text,
    copy_name,
    is_copy_name,
    latest_slot,
    missed_daily,
)
from nextron.domain.errors import DomainError

ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"
LA = "America/Los_Angeles"


def test_the_constants_are_the_specified_ones() -> None:
    assert (BACKUP_DB_JOB, BACKUP_FAILED, KIND_FIELD, LOCAL_DIR_NAME) == (
        "backup_db",
        "backup_failed",
        "kind",
        "local",
    )
    assert COUNTED_TABLES == ("suppression_entries", "provenance_records")
    assert DEFAULT_BACKUP_TIME == time(10, 0)
    assert (MIN_ROTATION_DAYS, MAX_ROTATION_DAYS, DEFAULT_ROTATION_DAYS) == (7, 35, 35)
    assert JOB_KINDS == frozenset({BackupKind.DAILY, BackupKind.MANUAL})
    assert [str(kind) for kind in BackupKind] == ["daily", "manual", "pre_migrate"]
    assert [str(status) for status in BackupStatus] == ["verified", "failed"]


@pytest.mark.parametrize(
    ("kind", "label"),
    [
        (BackupKind.DAILY, "daily"),
        (BackupKind.MANUAL, "manual"),
        (BackupKind.PRE_MIGRATE, "before a migration"),
    ],
)
def test_each_kind_has_its_label(kind: BackupKind, label: str) -> None:
    assert kind.label == label


# Copy names -------------------------------------------------------------------------------------


def test_copy_name_is_the_utc_time_with_milliseconds() -> None:
    at = datetime(2026, 11, 17, 10, 0, 0, 123456, tzinfo=UTC)

    assert copy_name("test", at) == "nextron-test-20261117T100000.123Z.db"


def test_copy_name_converts_to_utc_and_has_no_colon() -> None:
    at = datetime(2026, 11, 17, 2, 5, 9, 7000, tzinfo=ZoneInfo(LA))  # PST, UTC-8

    name = copy_name("prod", at)

    assert name == "nextron-prod-20261117T100509.007Z.db"
    assert ":" not in name


def test_copy_name_refuses_a_naive_time_and_a_bad_env() -> None:
    with pytest.raises(ValueError, match="aware"):
        copy_name("test", datetime(2026, 11, 17, 10, 0))
    with pytest.raises(ValueError, match="env"):
        copy_name("Prod", datetime(2026, 11, 17, 10, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="env"):
        copy_name("../x", datetime(2026, 11, 17, 10, 0, tzinfo=UTC))
    with pytest.raises(TypeError):
        copy_name("test", "2026-11-17")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("env", "name", "expected"),
    [
        ("test", "nextron-test-20261117T100000.123Z.db", True),
        ("prod", "nextron-prod-20261117T100000.000Z.db", True),
        ("test", "nextron-prod-20261117T100000.123Z.db", False),  # another environment's
        ("test", "nextron-test-20261117T100000Z.db", False),  # no milliseconds
        ("test", "nextron-test-20261117T100000.123Z.db-wal", False),
        ("test", "nextron.db", False),
        ("test", "nextron-test-20261117T100000.123Z.db.bak", False),
        ("test", "xnextron-test-20261117T100000.123Z.db", False),
        ("te.t", "nextron-test-20261117T100000.123Z.db", False),  # an env never a pattern
    ],
)
def test_is_copy_name_fullmatches_the_shape(env: str, name: str, expected: bool) -> None:
    assert is_copy_name(env, name) is expected


def test_is_copy_name_accepts_every_name_copy_name_makes() -> None:
    at = datetime(2026, 3, 8, 23, 59, 59, 999999, tzinfo=UTC)

    assert is_copy_name("dev", copy_name("dev", at))


# The schedule -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        # before today's 10:00 UTC: yesterday's
        (datetime(2026, 11, 17, 9, 59, 59, tzinfo=UTC), datetime(2026, 11, 16, 10, 0, tzinfo=UTC)),
        # exactly at it: today's
        (datetime(2026, 11, 17, 10, 0, tzinfo=UTC), datetime(2026, 11, 17, 10, 0, tzinfo=UTC)),
        # after it: today's
        (datetime(2026, 11, 17, 23, 0, tzinfo=UTC), datetime(2026, 11, 17, 10, 0, tzinfo=UTC)),
    ],
)
def test_latest_slot_in_utc(now: datetime, slot: datetime) -> None:
    assert latest_slot(now, time(10, 0), "UTC") == slot


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        # Los Angeles, PST (UTC-8): 10:00 local is 18:00 UTC
        (datetime(2026, 11, 17, 17, 59, tzinfo=UTC), datetime(2026, 11, 16, 18, 0, tzinfo=UTC)),
        (datetime(2026, 11, 17, 18, 0, tzinfo=UTC), datetime(2026, 11, 17, 18, 0, tzinfo=UTC)),
        (datetime(2026, 11, 18, 7, 0, tzinfo=UTC), datetime(2026, 11, 17, 18, 0, tzinfo=UTC)),
        # PDT (UTC-7): 10:00 local is 17:00 UTC
        (datetime(2026, 7, 1, 17, 0, tzinfo=UTC), datetime(2026, 7, 1, 17, 0, tzinfo=UTC)),
        # 2026-03-08 is the spring change: the day before is PST, the day itself PDT
        (datetime(2026, 3, 8, 16, 59, tzinfo=UTC), datetime(2026, 3, 7, 18, 0, tzinfo=UTC)),
        (datetime(2026, 3, 8, 17, 0, tzinfo=UTC), datetime(2026, 3, 8, 17, 0, tzinfo=UTC)),
        # 2026-11-01 is the autumn change: the day before is PDT, the day itself PST
        (datetime(2026, 11, 1, 17, 30, tzinfo=UTC), datetime(2026, 10, 31, 17, 0, tzinfo=UTC)),
        (datetime(2026, 11, 1, 18, 0, tzinfo=UTC), datetime(2026, 11, 1, 18, 0, tzinfo=UTC)),
    ],
)
def test_latest_slot_follows_the_local_time_across_daylight_saving(
    now: datetime, slot: datetime
) -> None:
    found = latest_slot(now, time(10, 0), LA)

    assert found == slot
    assert found.utcoffset() == timedelta(0)
    assert found.astimezone(ZoneInfo(LA)).time() == time(10, 0)


def test_latest_slot_takes_any_aware_now() -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone(timedelta(hours=5)))  # 07:00 UTC

    assert latest_slot(now, time(10, 0), "UTC") == datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def test_latest_slot_refuses_a_naive_now() -> None:
    with pytest.raises(ValueError, match="aware"):
        latest_slot(datetime(2026, 11, 17, 10, 0), time(10, 0), "UTC")


@pytest.mark.parametrize(
    ("newest", "missed"),
    [
        (None, False),  # no daily backup yet: a new install waits for its first time
        (datetime(2026, 11, 16, 9, 59, tzinfo=UTC), True),  # before yesterday's slot
        (datetime(2026, 11, 16, 10, 0, tzinfo=UTC), False),  # at the latest slot
        (datetime(2026, 11, 16, 23, 0, tzinfo=UTC), False),  # after it
    ],
)
def test_missed_daily(newest: datetime | None, missed: bool) -> None:
    now = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)  # the latest slot is 2026-11-16 10:00

    assert missed_daily(newest, now, time(10, 0), "UTC") is missed


def test_missed_daily_after_a_shutdown_across_the_slot() -> None:
    newest = datetime(2026, 11, 16, 10, 0, 5, tzinfo=UTC)  # yesterday's run
    now = datetime(2026, 11, 17, 10, 30, tzinfo=UTC)  # the PC was off at today's 10:00

    assert missed_daily(newest, now, time(10, 0), "UTC")


def test_backup_schedule_is_frozen() -> None:
    schedule = BackupSchedule(time(10, 0), "UTC", missed=False)

    with pytest.raises(AttributeError):
        schedule.missed = True  # type: ignore[misc]


# Errors and text --------------------------------------------------------------------------------


def test_backup_not_verified_is_a_domain_error_whose_message_is_the_reason() -> None:
    error = BackupNotVerified("the copy has no Alembic revision")

    assert isinstance(error, DomainError)
    assert error.code == "backup_not_verified"
    assert (error.message, str(error)) == ("the copy has no Alembic revision",) * 2


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (BackupKind.DAILY, f"Backup failed: daily {ULID}"),
        ("manual", f"Backup failed: manual {ULID}"),
        (BackupKind.PRE_MIGRATE, f"Backup failed: pre_migrate {ULID}"),
    ],
)
def test_backup_failed_text_is_ids_and_codes_only(kind: str, text: str) -> None:
    assert backup_failed_text(kind, ULID) == text


def test_backup_failed_text_refuses_anything_but_a_kind_and_a_ulid() -> None:
    with pytest.raises(ValueError):
        backup_failed_text("weekly", ULID)
    with pytest.raises(ValueError):
        backup_failed_text("daily", "not-a-ulid")
